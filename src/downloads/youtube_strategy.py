"""Адаптивный порядок YouTube-стратегий и классификация сетевых ошибок.

Автоматически выделено из прежнего модуля video_download.py без изменения тел методов.
"""

from typing import Dict
from typing import List
from typing import Tuple

from src.core.constants import YT_DLP_ADAPTIVE_PRIMARY_FAILURE_THRESHOLD, YT_DLP_FAST_CONCURRENT_FRAGMENTS, YT_DLP_FAST_FAIL_ENABLED, YT_DLP_MAX_NETWORK_FAILURES_PER_VIDEO, YT_DLP_MAX_SLOW_NETWORK_FAILURES_PER_VIDEO, YT_DLP_SLOW_NETWORK_FAILURE_SEC


class YouTubeStrategyMixin:
    def get_ytdlp_fast_fragments(self) -> str:
        """Через VPN/TUN два видео в два потока уже дают параллельность.

        Если оставить ещё и --concurrent-fragments=2 внутри каждого yt-dlp,
        получается до четырёх одновременных соединений к googlevideo.com. По
        логам это быстрее на старте, но чаще заканчивается timeout/0.00B/s.
        """
        try:
            return "1" if int(getattr(self, "max_concurrent", 1)) >= 2 else YT_DLP_FAST_CONCURRENT_FRAGMENTS
        except Exception:
            return "1"


    def _is_youtube_auth_or_forbidden_error(self, text: str) -> bool:
        lowered = str(text or "").lower()
        markers = (
            "sign in to confirm",
            "not a bot",
            "login required",
            "http error 403",
            "forbidden",
            "unable to download api page",
        )
        return any(marker in lowered for marker in markers)


    def _is_youtube_network_error(self, text: str) -> bool:
        lowered = str(text or "").casefold()
        markers = (
            "connection aborted", "connectionreseterror",
            "connection reset by peer", "timeout", "timed out",
            "unable to download webpage", "read timed out",
            "http error 416", "http error 429", "http error 500",
            "http error 502", "http error 503", "http error 504",
            "ssl:", "unexpected_eof", "eof occurred",
            "handshake failure", "aria2c exited",
            "connect timeout", "connection to", "remote end closed",
            "incomplete read", "temporarily unavailable", "winerror 10054",
            "удаленный хост принудительно разорвал",
            "удалённый хост принудительно разорвал",
        )
        return any(marker in lowered for marker in markers)


    def _updated_youtube_network_failure_counts(
        self,
        error_text: str,
        attempt_elapsed: float,
        network_failure_count: int,
        slow_network_failure_count: int,
    ) -> Tuple[int, int, bool]:
        is_network_error = self._is_youtube_network_error(error_text)
        if not is_network_error:
            return network_failure_count, slow_network_failure_count, False
        network_failure_count += 1
        if attempt_elapsed >= YT_DLP_SLOW_NETWORK_FAILURE_SEC:
            slow_network_failure_count += 1
        return network_failure_count, slow_network_failure_count, True


    def _should_fast_fail_youtube_network(
        self,
        network_failure_count: int,
        slow_network_failure_count: int,
    ) -> bool:
        return bool(
            YT_DLP_FAST_FAIL_ENABLED
            and (
                network_failure_count >= YT_DLP_MAX_NETWORK_FAILURES_PER_VIDEO
                or slow_network_failure_count
                >= YT_DLP_MAX_SLOW_NETWORK_FAILURES_PER_VIDEO
            )
        )


    def _note_youtube_primary_strategy_result(self, success: bool,
                                              error_text: str = "") -> None:
        with self.youtube_strategy_state_lock:
            if success:
                self.youtube_primary_success_count += 1
                self.youtube_primary_auth_failure_count = 0
            elif self._is_youtube_auth_or_forbidden_error(error_text):
                self.youtube_primary_auth_failure_count += 1


    def _apply_adaptive_youtube_strategy_order(self,
                                               strategies: List[Dict]) -> List[Dict]:
        """После серии web-блокировок ставит рабочие Android-варианты первыми."""
        with self.youtube_strategy_state_lock:
            enabled = (
                self.youtube_primary_auth_failure_count
                >= YT_DLP_ADAPTIVE_PRIMARY_FAILURE_THRESHOLD
            )
            trigger_failure_count = self.youtube_primary_auth_failure_count
            should_log = enabled and not self.youtube_adaptive_strategy_logged
            if should_log:
                self.youtube_adaptive_strategy_logged = True
        if not enabled:
            return strategies

        # Даже после 403/проверок «не робот» сначала сохраняем шанс на 1080p.
        # Progressive MP4 (обычно 720p/360p) остаётся только финальным fallback.
        preferred_names = (
            "Android Client small chunks",
            "Android Client tiny chunks",
            "EJS GitHub stable auto quality",
        )
        preferred = [
            item
            for name in preferred_names
            for item in strategies
            if item.get("name") == name
        ]
        remaining = [
            item for item in strategies
            if item.get("name") not in preferred_names
        ]
        reordered = preferred + remaining
        if should_log:
            self.log(
                "🧭 После повторных 403/проверок «не робот» сначала пробую "
                "Android/обычные DASH-варианты до 1080p; цельный MP4 оставляю на финальный fallback",
                "INFO"
            )
            self.record_problem(
                "Включён адаптивный порядок стратегий YouTube",
                "INFO",
                "youtube_adaptive_strategy_order",
                {
                    "trigger": "repeated_youtube_auth_or_forbidden",
                    "trigger_failure_count": trigger_failure_count,
                    "threshold": YT_DLP_ADAPTIVE_PRIMARY_FAILURE_THRESHOLD,
                    "preferred_order": list(preferred_names),
                    "original_first_strategies": [
                        item.get("name") for item in strategies[:6]
                    ],
                    "effective_first_strategies": [
                        item.get("name") for item in reordered[:6]
                    ],
                },
            )
        return reordered
