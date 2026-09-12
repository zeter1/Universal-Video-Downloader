"""Каталог стратегий yt-dlp и сетевых профилей.

Owner-файл для порядка fallback-стратегий, YouTube clients, chunk/retry профилей
и aria2c. Основной workflow скачивания намеренно не хранит этот большой список.
"""

import shutil

from src.core.constants import YT_DLP_HTTP_CHUNK_SIZE, YT_DLP_SAFE_CONCURRENT_FRAGMENTS, YT_DLP_SAFE_HTTP_CHUNK_SIZE, YT_DLP_SLOW_FRAGMENT_RETRIES, YT_DLP_SLOW_RETRIES, YT_DLP_SLOW_SOCKET_TIMEOUT, YT_DLP_TINY_HTTP_CHUNK_SIZE


def build_ytdlp_strategy_plan(app, url: str, quality: str):
    # Быстрая первая попытка: несколько фрагментов и крупнее chunk.
    # Если сеть/YouTube начнут рвать соединение — ниже идут более осторожные
    # fallback-стратегии с мелкими chunk и 1 фрагментом.
    fast_fragments = app.get_ytdlp_fast_fragments()
    default_network_args = [
        "--http-chunk-size", YT_DLP_HTTP_CHUNK_SIZE,
        "--concurrent-fragments", fast_fragments,
    ]
    default_ipv4_network_args = [
        "--http-chunk-size", YT_DLP_HTTP_CHUNK_SIZE,
        "--concurrent-fragments", fast_fragments,
        "--force-ipv4",
    ]
    default_ipv6_network_args = [
        "--http-chunk-size", YT_DLP_SAFE_HTTP_CHUNK_SIZE,
        "--concurrent-fragments", YT_DLP_SAFE_CONCURRENT_FRAGMENTS,
        "--force-ipv6",
    ]
    safe_chunk_network_args = [
        "--http-chunk-size", YT_DLP_SAFE_HTTP_CHUNK_SIZE,
        "--concurrent-fragments", YT_DLP_SAFE_CONCURRENT_FRAGMENTS,
        "--force-ipv4",
    ]
    no_chunk_network_args = [
        "--concurrent-fragments", YT_DLP_SAFE_CONCURRENT_FRAGMENTS,
        "--force-ipv4",
    ]
    tiny_chunk_network_args = [
        "--http-chunk-size", YT_DLP_TINY_HTTP_CHUNK_SIZE,
        "--concurrent-fragments", YT_DLP_SAFE_CONCURRENT_FRAGMENTS,
        "--force-ipv4",
    ]
    no_force_ipv4_network_args = [
        "--concurrent-fragments", YT_DLP_SAFE_CONCURRENT_FRAGMENTS,
    ]
    neutral_network_args = [
        "--http-chunk-size", YT_DLP_SAFE_HTTP_CHUNK_SIZE,
        "--concurrent-fragments", YT_DLP_SAFE_CONCURRENT_FRAGMENTS,
    ]
    neutral_tiny_chunk_network_args = [
        "--http-chunk-size", YT_DLP_TINY_HTTP_CHUNK_SIZE,
        "--concurrent-fragments", YT_DLP_SAFE_CONCURRENT_FRAGMENTS,
    ]
    aria2_downloader_args = []
    if shutil.which("aria2c"):
        aria2_downloader_args = [
            "--downloader", "aria2c",
            "--downloader-args",
            (
                "aria2c:-x 1 -s 1 -j 1 -k 1M --max-tries=8 "
                "--retry-wait=2 --connect-timeout=20 --timeout=30 "
                "--summary-interval=0 --console-log-level=warn"
            ),
        ]

    strategies = [
        # Сначала пробуем 1080p/лучшее до 1080p, но только одной осторожной DASH-попыткой.
        # В свежих логах две параллельные DASH-загрузки стабильно ловили Read timed out
        # с rr*.googlevideo.com. Поэтому после первой сетевой ошибки быстро уходим
        # на цельный MP4-поток и другие клиенты YouTube, а не тратим минуты на тот же CDN.
        {'name': 'EJS GitHub stable auto quality', 'fallback_format': False,
         'diagnostic_verbose': True,
         'args': [], 'network_args': default_network_args, 'ejs_mode': 'github'},
        # Свежая сессия: Android progressive восстановил 5/5 загрузок,
        # обычный early progressive был успешен только в 1/6 попыток.
        {'name': 'Android Progressive MP4 early', 'fallback_format': False,
         'progressive_format': True,
         'args': ["--extractor-args", "youtube:player_client=android"],
         'network_args': no_force_ipv4_network_args, 'ejs_mode': 'github'},
        {'name': 'Early progressive MP4 no force IPv4', 'fallback_format': False,
         'progressive_format': True, 'args': [], 'network_args': no_force_ipv4_network_args,
         'ejs_mode': 'github'},
        {'name': 'EJS GitHub stable auto quality IPv4', 'fallback_format': False,
         'args': [], 'network_args': default_ipv4_network_args, 'ejs_mode': 'github'},
        {'name': 'EJS GitHub stable auto quality IPv6', 'fallback_format': False,
         'args': [], 'network_args': default_ipv6_network_args, 'ejs_mode': 'github'},
        {'name': 'Early progressive MP4 IPv4 tiny chunks', 'fallback_format': False,
         'progressive_format': True, 'args': [], 'network_args': tiny_chunk_network_args,
         'ejs_mode': 'github'},
        {'name': 'Slow direct progressive MP4 90 sec timeout', 'fallback_format': False,
         'progressive_format': True, 'args': [], 'network_args': tiny_chunk_network_args,
         'ejs_mode': 'github', 'socket_timeout': YT_DLP_SLOW_SOCKET_TIMEOUT,
         'retries': YT_DLP_SLOW_RETRIES, 'fragment_retries': YT_DLP_SLOW_FRAGMENT_RETRIES},
        # Если GitHub release assets недоступны, Deno умеет тянуть EJS через npm.
        {'name': 'EJS npm stable auto quality', 'fallback_format': False,
         'args': [], 'network_args': neutral_network_args, 'ejs_mode': 'npm'},
        {'name': 'EJS GitHub safe tiny chunks', 'fallback_format': False,
         'args': [], 'network_args': tiny_chunk_network_args, 'ejs_mode': 'github'},
        # Быстрый практичный fallback: один MP4-поток со звуком. Часто это 720p/360p,
        # зато он обходит часть проблем с раздельными DASH audio/video.
        {'name': 'Progressive MP4 single file', 'fallback_format': False,
         'progressive_format': True, 'args': [], 'network_args': no_chunk_network_args,
         'ejs_mode': 'github'},
        {'name': 'Android Progressive MP4 single file', 'fallback_format': False,
         'progressive_format': True,
         'args': ["--extractor-args", "youtube:player_client=android"],
         'network_args': no_chunk_network_args, 'ejs_mode': 'github'},
        {'name': 'Android Client small chunks', 'fallback_format': False,
         'args': ["--extractor-args", "youtube:player_client=android"],
         'network_args': safe_chunk_network_args, 'ejs_mode': 'github'},
        {'name': 'Android Client tiny chunks', 'fallback_format': False,
         'args': ["--extractor-args", "youtube:player_client=android"],
         'network_args': tiny_chunk_network_args, 'ejs_mode': 'github'},
        {'name': 'iOS Client without HTTP chunks', 'fallback_format': False,
         'args': ["--extractor-args", "youtube:player_client=ios"],
         'network_args': no_chunk_network_args, 'ejs_mode': 'github'},
        {'name': 'TV Embedded Client without HTTP chunks', 'fallback_format': False,
         'args': ["--extractor-args", "youtube:player_client=tv_embedded"],
         'network_args': no_chunk_network_args, 'ejs_mode': 'github'},
        {'name': 'Web Embedded Client without HTTP chunks', 'fallback_format': False,
         'args': ["--extractor-args", "youtube:player_client=web_embedded"],
         'network_args': no_chunk_network_args, 'ejs_mode': 'github'},
        {'name': 'Default Client without HTTP chunks', 'fallback_format': False,
         'args': [], 'network_args': no_chunk_network_args, 'ejs_mode': 'github'},
        {'name': 'Default Client no force IPv4', 'fallback_format': False,
         'args': [], 'network_args': no_force_ipv4_network_args, 'ejs_mode': 'github'},
        {'name': 'Default Client small chunks no force IPv4', 'fallback_format': False,
         'args': [], 'network_args': neutral_network_args, 'ejs_mode': 'github'},
        # Any-protocol 1080p is intentionally late. Current YouTube can expose
        # fragmented HLS video that downloads fully but fails in FFmpeg merge.
        # We first exhaust direct HTTP/DASH 1080p clients, then try one verbose
        # any-format attempt so diagnostics capture the exact ffmpeg failure.
        {'name': 'Slow direct any format 90 sec timeout', 'fallback_format': True,
         'diagnostic_verbose': True,
         'args': [], 'network_args': tiny_chunk_network_args,
         'ejs_mode': 'github', 'socket_timeout': YT_DLP_SLOW_SOCKET_TIMEOUT,
         'retries': YT_DLP_SLOW_RETRIES, 'fragment_retries': YT_DLP_SLOW_FRAGMENT_RETRIES},
        {'name': 'Any Format iOS without HTTP chunks', 'fallback_format': True,
         'args': ["--extractor-args", "youtube:player_client=ios"],
         'network_args': no_chunk_network_args, 'ejs_mode': 'github'},
        {'name': 'Any Format Mobile Web without HTTP chunks', 'fallback_format': True,
         'args': ["--extractor-args", "youtube:player_client=mweb"],
         'network_args': no_chunk_network_args, 'ejs_mode': 'github'},
        {'name': 'Default + Chrome Cookies without HTTP chunks', 'fallback_format': False,
         'args': ["--cookies-from-browser", "chrome"],
         'network_args': no_chunk_network_args, 'ejs_mode': 'github'},
        {'name': 'Any Format Default small chunks', 'fallback_format': True,
         'args': [], 'network_args': neutral_network_args, 'ejs_mode': 'github'},
        {'name': 'Any Format Default tiny chunks no force IPv4', 'fallback_format': True,
         'args': [], 'network_args': neutral_tiny_chunk_network_args, 'ejs_mode': 'github'},
        {'name': 'Any Format Chrome Cookies small chunks', 'fallback_format': True,
         'args': ["--cookies-from-browser", "chrome"],
         'network_args': neutral_network_args, 'ejs_mode': 'github'},
        # Последняя страховка: без EJS, чтобы скачать хотя бы доступный
        # progressive/низкий формат, если remote-components не скачиваются.
        {'name': 'Emergency progressive without EJS remote components', 'fallback_format': False,
         'progressive_format': True,
         'args': ["--extractor-args", "youtube:player_client=default"],
         'network_args': no_chunk_network_args, 'ejs_mode': 'none'},
    ]
    # 1080p-first: все стратегии, способные скачать раздельные video+audio
    # потоки, выполняются раньше цельных progressive MP4. Это критично для
    # YouTube, где 1080p обычно доступно именно как video-only + audio-only.
    if quality != "360p":
        high_quality_strategies = [s for s in strategies if not s.get('progressive_format')]
        progressive_strategies = [s for s in strategies if s.get('progressive_format')]
        strategies = high_quality_strategies + progressive_strategies

    if quality == "360p":
        # 360p обычно нужен для быстрого и лёгкого скачивания.
        # В прошлой логике даже при 360p первые 4 попытки качали раздельные DASH-потоки
        # video+audio с googlevideo.com. В свежих логах именно эти DASH-ссылки стабильно
        # рвались с ConnectionResetError(10054), а программа до цельного MP4-потока
        # просто не успевала дойти до ручной остановки.
        #
        # Поэтому для 360p сначала пробуем progressive MP4: один готовый поток со звуком
        # (обычно itag 18). Это меньше запросов к CDN, быстрее стартует и чаще проходит
        # при нестабильном соединении. DASH/лучшее качество остаются ниже как fallback.
        low_quality_first = [
            {'name': '360p Progressive MP4 first no force IPv4', 'fallback_format': False,
             'progressive_format': True, 'args': [],
             'network_args': no_force_ipv4_network_args, 'ejs_mode': 'github'},
            {'name': '360p Progressive MP4 first tiny chunks no force IPv4', 'fallback_format': False,
             'progressive_format': True, 'args': [],
             'network_args': neutral_tiny_chunk_network_args, 'ejs_mode': 'github'},
            {'name': '360p Progressive MP4 first IPv4', 'fallback_format': False,
             'progressive_format': True, 'args': [],
             'network_args': no_chunk_network_args, 'ejs_mode': 'github'},
        ]
        existing_strategy_names = {s.get('name') for s in low_quality_first}
        strategies = low_quality_first + [
            s for s in strategies if s.get('name') not in existing_strategy_names
        ]

    if aria2_downloader_args:
        if app.is_youtube_url(url):
            if not getattr(app, "aria2c_youtube_skip_logged", False):
                app.aria2c_youtube_skip_logged = True
                app.file_logger.info(
                    "aria2c найден, но пропущен для YouTube: свежие логи показывают "
                    "повторные SSL/TLS разрывы googlevideo.com через внешний загрузчик"
                )
        else:
            strategies.append(
                {'name': 'Default Client via aria2c', 'fallback_format': False,
                 'args': aria2_downloader_args, 'network_args': neutral_network_args}
            )

    if app.is_youtube_url(url):
        strategies = app._apply_adaptive_youtube_strategy_order(strategies)

    return strategies, default_network_args, fast_fragments
