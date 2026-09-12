"""Основной цикл yt-dlp: максимум 1080p, поиск 1080p до низкого fallback.

Автоматически выделено из прежнего модуля video_download.py без изменения тел методов.
"""

from typing import Dict, List, Optional
import shutil
import subprocess
import time
import traceback

from src.core.constants import DOWNLOAD_TIMEOUT_SEC, YT_DLP_NETWORK_RETRY_PAUSE_SEC
from src.core.errors import CommandCancelledError, ProblematicDownloadSkipped
from src.downloads.youtube_attempt import build_attempt_context, log_first_attempt, prepare_download_setup
from src.downloads.youtube_command import build_ytdlp_download_command
from src.downloads.youtube_failure_reporting import report_failed_attempt
from src.downloads.youtube_finalize import (
    finalize_lower_quality_candidate, record_all_strategies_failed,
)
from src.downloads.youtube_success import handle_successful_attempt
from src.downloads.youtube_recovery import (
    handle_postprocessing_failure, record_orchestration_exception,
)
from src.downloads.youtube_retry import (
    cookie_read_failed, has_auth_error, has_critical_error, has_youtube_retry_error,
)
from src.downloads.youtube_strategy_catalog import build_ytdlp_strategy_plan


class YouTubeDownloaderMixin:
    def method_yt_dlp(self, url: str) -> Optional[str]:
        setup = prepare_download_setup(self, url)
        video_dir = setup.video_dir
        quality = setup.quality
        expected_video_id = setup.expected_video_id
        download_temp_dir = setup.download_temp_dir
        video_files_before = setup.video_files_before
        quality_candidate_dir = setup.quality_candidate_dir
        proxy_args = setup.proxy_args
        proxy_enabled = setup.proxy_enabled
        proxy_url_masked = setup.proxy_url_masked
        video_path = None
        best_lower_candidate_path: Optional[str] = None
        best_lower_candidate_height = -1
        best_lower_candidate_width = -1
        force_progressive_fallback = False
        skip_risky_any_formats = False
        postprocessing_failure_count = 0
        strategies, default_network_args, fast_fragments = build_ytdlp_strategy_plan(
            self, url, quality
        )
        success_video = False
        last_error    = None
        network_failure_count = 0
        slow_network_failure_count = 0
        attempt_summaries: List[Dict] = []
        for attempt, strategy in enumerate(strategies, 1):
            if success_video or self.cancel_flag.is_set():
                break
            if force_progressive_fallback and not strategy.get('progressive_format'):
                continue
            if skip_risky_any_formats and strategy.get('fallback_format'):
                continue
            # Все high-quality варианты уже были пройдены. Если у нас есть
            # проверенный резерв ниже 1080p, повторно качать progressive 720/360
            # нет смысла — используем лучший уже найденный файл.
            if best_lower_candidate_path and strategy.get('progressive_format'):
                break
            (
                cmd_v,
                ejs_args,
                strategy_retries,
                strategy_fragment_retries,
                strategy_socket_timeout,
            ) = build_ytdlp_download_command(
                self, url, strategy, default_network_args, download_temp_dir, proxy_args
            )
            if attempt > 1:
                self.log(f"🔄 Попытка {attempt}/{len(strategies)}: {strategy['name']}", "INFO")
            try:
                if attempt == 1:
                    log_first_attempt(
                        self, quality, fast_fragments, strategy, strategy_retries,
                        strategy_fragment_retries, strategy_socket_timeout,
                        proxy_enabled, proxy_url_masked,
                    )

                attempt_context = build_attempt_context(
                    self,
                    url=url, attempt=attempt, strategy=strategy, quality=quality,
                    expected_video_id=expected_video_id, download_temp_dir=download_temp_dir,
                    video_dir=video_dir, strategy_count=len(strategies),
                    default_network_args=default_network_args,
                    strategy_socket_timeout=strategy_socket_timeout,
                    strategy_retries=strategy_retries,
                    strategy_fragment_retries=strategy_fragment_retries,
                    proxy_enabled=proxy_enabled, proxy_url_masked=proxy_url_masked,
                    ejs_args=ejs_args,
                )
                attempt_started = time.time()
                res_v = self.run_command(
                    cmd_v, DOWNLOAD_TIMEOUT_SEC, "yt_dlp_download", attempt_context
                )
                attempt_elapsed = time.time() - attempt_started

                if self.cancel_flag.is_set():
                    self.log("🛑 Загрузка остановлена пользователем", "WARNING")
                    break

                if res_v.returncode == 0:
                    decision = handle_successful_attempt(
                        self, result=res_v, url=url, attempt=attempt, strategy=strategy,
                        command=cmd_v, download_temp_dir=download_temp_dir,
                        video_dir=video_dir, quality_candidate_dir=quality_candidate_dir,
                        video_files_before=video_files_before, expected_video_id=expected_video_id,
                        best_candidate_path=best_lower_candidate_path,
                        best_candidate_height=best_lower_candidate_height,
                        best_candidate_width=best_lower_candidate_width,
                        attempt_summaries=attempt_summaries,
                    )
                    if decision.last_error:
                        last_error = decision.last_error
                    if decision.candidate_path is not None:
                        best_lower_candidate_path = decision.candidate_path
                        best_lower_candidate_height = decision.candidate_height
                        best_lower_candidate_width = decision.candidate_width
                    if decision.action == "fatal":
                        return None
                    if decision.action == "success":
                        video_path = decision.video_path
                        success_video = True
                        break
                    continue
                else:
                    failure = report_failed_attempt(
                        self, url=url, attempt=attempt, strategy=strategy, command=cmd_v,
                        result=res_v, elapsed_sec=attempt_elapsed,
                        socket_timeout=strategy_socket_timeout, proxy_enabled=proxy_enabled,
                        proxy_url_masked=proxy_url_masked,
                        network_failure_count=network_failure_count,
                        slow_network_failure_count=slow_network_failure_count,
                        attempt_summaries=attempt_summaries, strategy_count=len(strategies),
                        download_temp_dir=download_temp_dir, expected_video_id=expected_video_id,
                    )
                    last_error = failure.last_error
                    stderr_lower = failure.error_lower
                    network_failure_count = failure.network_failure_count
                    slow_network_failure_count = failure.slow_network_failure_count
                    is_network_error = failure.is_network_error
                    failure_analysis = failure.failure_analysis

                    if failure_analysis.get("stage") == "postprocessing_merge":
                        (
                            postprocessing_failure_count,
                            skip_risky_any_formats,
                            force_progressive_fallback,
                        ) = handle_postprocessing_failure(
                            self, url=url, attempt=attempt, strategy=strategy, command=cmd_v,
                            result=res_v, failure_analysis=failure_analysis,
                            attempt_summaries=attempt_summaries,
                            download_temp_dir=download_temp_dir,
                            expected_video_id=expected_video_id,
                            failure_count=postprocessing_failure_count,
                            skip_risky_any_formats=skip_risky_any_formats,
                            force_progressive_fallback=force_progressive_fallback,
                        )
                        continue

                    if has_auth_error(stderr_lower):
                        if attempt < len(strategies):
                            cookie_strategy_used = any(
                                arg in cmd_v for arg in (
                                    "--cookies-from-browser", "--cookies"
                                )
                            )
                            if cookie_strategy_used and cookie_read_failed(stderr_lower):
                                self.log(
                                    "🔐 Не удалось прочитать cookies браузера; "
                                    "пробую следующую стратегию...",
                                    "WARNING"
                                )
                            else:
                                self.log(
                                    "🔐 YouTube запросил авторизацию или проверку «не робот»; "
                                    "пробую другую стратегию...",
                                    "WARNING"
                                )
                            if self.cancel_flag.is_set():
                                break
                            continue
                        self.log(
                            "❌ Не удалось скачать: нужна авторизация или cookies браузера",
                            "ERROR"
                        )
                        break

                    if "gvs po token" in stderr_lower or "po token" in stderr_lower:
                        if attempt < len(strategies):
                            self.log(
                                "⚠️ YouTube GVS PO Token warning: это проблема конкретного клиента/формата, "
                                "а не доказательство недоступности видео. Пробую следующую стратегию...",
                                "WARNING"
                            )
                            if self.cancel_flag.is_set():
                                break
                            continue

                    if has_critical_error(stderr_lower):
                        self.log(f"❌ Видео недоступно: {last_error[:200]}", "ERROR")
                        break

                    if is_network_error:
                        if network_failure_count == 3 and not proxy_enabled:
                            self.log(
                                "🌐 Уже 3 одинаковых сетевых сбоя googlevideo.com без proxy. "
                                "Это похоже не на качество/формат, а на сетевой путь до CDN. "
                                "Попробуйте включить VPN/proxy и прописать proxy в настройках.",
                                "WARNING"
                            )
                            self.record_problem(
                                "Повторные Read timed out/SSL/10054 от googlevideo.com без proxy",
                                "WARNING", "yt_dlp_repeated_googlevideo_network_errors_no_proxy",
                                {
                                    "url": url,
                                    "attempt": attempt,
                                    "network_failure_count": network_failure_count,
                                    "attempt_summaries": attempt_summaries[-5:],
                                    "recommendation": (
                                        "Добавить/включить proxy/VPN. Для локального proxy обычно подходят "
                                        "socks5://127.0.0.1:1080 или http://127.0.0.1:7890."
                                    ),
                                },
                                command=cmd_v, stdout=res_v.stdout, stderr=res_v.stderr
                            )

                        too_many_network_failures = (
                            self._should_fast_fail_youtube_network(
                                network_failure_count,
                                slow_network_failure_count,
                            )
                        )
                        if too_many_network_failures:
                            if not strategy.get('progressive_format'):
                                force_progressive_fallback = True
                                self.log(
                                    "⚠️ High-quality DASH повторно рвётся по сети. "
                                    "Не бросаю видео: перехожу сразу к цельному MP4 fallback.",
                                    "WARNING"
                                )
                                self.record_problem(
                                    "После повторных сетевых разрывов high-quality переключён на progressive fallback",
                                    "WARNING", "yt_dlp_network_fast_fail_to_progressive",
                                    {
                                        "url": url,
                                        "attempt": attempt,
                                        "strategy": strategy["name"],
                                        "attempt_elapsed_sec": round(attempt_elapsed, 2),
                                        "network_failure_count": network_failure_count,
                                        "slow_network_failure_count": slow_network_failure_count,
                                        "best_lower_candidate_height": best_lower_candidate_height,
                                        "last_error": last_error,
                                    },
                                    command=cmd_v, stdout=res_v.stdout, stderr=res_v.stderr
                                )
                                continue
                            self.log(
                                "⚠️ Сеть повторно рвала соединение даже на progressive fallback; "
                                "останавливаю попытки для этого видео",
                                "WARNING"
                            )
                            break

                        if attempt < len(strategies):
                            self.log(
                                "⚠️ Сетевая ошибка/тайм-аут googlevideo.com. "
                                f"Пауза {YT_DLP_NETWORK_RETRY_PAUSE_SEC} сек, затем следующая стратегия "
                                f"({network_failure_count} сетевых сбоев по этому видео)...",
                                "WARNING"
                            )
                            if self.cancel_flag.wait(YT_DLP_NETWORK_RETRY_PAUSE_SEC):
                                break
                            continue
                        break

                    if has_youtube_retry_error(stderr_lower):
                        if attempt < len(strategies):
                            self.log("⚠️ YouTube блокировка, пробую другой метод...", "WARNING")
                            if self.cancel_flag.is_set():
                                break
                            continue
                        else:
                            self.log(
                                f"❌ Все {len(strategies)} стратегий заблокированы YouTube", "ERROR"
                            )
                            self.log("💡 Попробуйте обновить yt-dlp: pip install -U yt-dlp", "INFO")
                            break
                    else:
                        self.log(f"❌ Ошибка (код {res_v.returncode}): {last_error[:250]}", "ERROR")
                        if attempt < len(strategies):
                            if self.cancel_flag.wait(3):
                                break
                            continue
                        break

            except ProblematicDownloadSkipped:
                # Это не ошибка всей программы: ролик слишком медленный на текущем VPN/CDN.
                # Прекращаем перебор стратегий для него и отдаём в download_single_video,
                # чтобы ссылка попала в отдельный txt внутри «Обработать вручную».
                self.cleanup_download_temp_files(
                    download_temp_dir, expected_video_id=expected_video_id,
                    final_video_path=None, reason="problematic_slow_download_skip"
                )
                try:
                    shutil.rmtree(download_temp_dir, ignore_errors=True)
                except Exception:
                    pass
                raise
            except CommandCancelledError:
                self.log("🛑 Загрузка остановлена пользователем", "WARNING")
                break
            except subprocess.TimeoutExpired:
                # run_command уже остановил процесс и записал подробности в Логи проблем.
                self.log(f"⏰ Тайм-аут загрузки (>{DOWNLOAD_TIMEOUT_SEC // 60} мин)", "ERROR")
                if attempt < len(strategies):
                    continue
                break
            except KeyboardInterrupt:
                self.log("🛑 Загрузка прервана пользователем", "WARNING")
                raise
            except Exception as e:
                self.log(f"❌ Неожиданная ошибка: {str(e)}", "ERROR")
                self.file_logger.error(f"method_yt_dlp: {traceback.format_exc()}")
                record_orchestration_exception(
                    self, error=e, url=url, attempt=attempt, strategy=strategy,
                    quality=quality, expected_video_id=expected_video_id,
                    download_temp_dir=download_temp_dir, command=cmd_v,
                    postprocessing_failure_count=postprocessing_failure_count,
                    force_progressive_fallback=force_progressive_fallback,
                    skip_risky_any_formats=skip_risky_any_formats,
                )
                if attempt < len(strategies):
                    if self.cancel_flag.wait(2):
                        break
                    continue
                break

        if not success_video and not self.cancel_flag.is_set() and best_lower_candidate_path:
            fallback_path = finalize_lower_quality_candidate(
                self, url=url, candidate_path=best_lower_candidate_path,
                candidate_width=best_lower_candidate_width,
                candidate_height=best_lower_candidate_height, video_dir=video_dir,
                expected_video_id=expected_video_id,
            )
            if fallback_path:
                video_path = fallback_path
                success_video = True
                shutil.rmtree(quality_candidate_dir, ignore_errors=True)

        if not success_video and not self.cancel_flag.is_set():
            self.cleanup_download_temp_files(
                download_temp_dir, expected_video_id=expected_video_id,
                final_video_path=None, reason="download_failed_all_strategies"
            )
            try:
                shutil.rmtree(download_temp_dir, ignore_errors=True)
                shutil.rmtree(quality_candidate_dir, ignore_errors=True)
            except Exception:
                pass
            record_all_strategies_failed(
                self, url=url, quality=quality, expected_video_id=expected_video_id,
                last_error=last_error, strategy_count=len(strategies),
                proxy_enabled=proxy_enabled, proxy_url_masked=proxy_url_masked,
                attempt_summaries=attempt_summaries, fast_fragments=fast_fragments,
            )

        return video_path if success_video else None
