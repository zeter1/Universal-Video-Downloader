import math
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from src.application import VideoDownloader
from src.core.session_settings import session_setting
from src.downloads.youtube_command import video_format_args


class QualityTests(unittest.TestCase):
    def select(self, formats, progressive=False):
        try:
            from yt_dlp import YoutubeDL
        except ImportError:
            self.skipTest('yt-dlp is required for format-selection integration tests')
        args = video_format_args(progressive)
        with YoutubeDL({'quiet': True, 'no_warnings': True, 'skip_download': True,
                        'format': args[1], 'format_sort_force': True,
                        'format_sort': args[-1].split(',')}) as ydl:
            return ydl.process_ie_result({'id': 'fixture', 'title': 'fixture',
                                          'formats': formats}, download=False)

    def formats(self):
        def f(id, h, br, ext='mp4', audio=False):
            return dict(format_id=id, url='https://example.invalid/'+id, protocol='https',
                        height=h, width=h*16//9 if h else None, vbr=br,
                        ext=ext, vcodec='h264' if h else 'none',
                        acodec='aac' if audio or not h else 'none',
                        abr=128 if audio or not h else 0)
        return [f('mp4-720',720,5000),f('webm-1080-low',1080,1500,'webm'),
                f('webm-1080-best',1080,4500,'webm'),f('4k',2160,9000),
                f('audio',0,0,'m4a'),f('combined',360,400,audio=True)]

    def test_1080_and_highest_bitrate_beat_mp4_and_4k(self):
        result = self.select(self.formats())
        self.assertEqual([x['format_id'] for x in result['requested_formats']],
                         ['webm-1080-best','audio'])

    def test_best_available_below_limit(self):
        result = self.select([f for f in self.formats() if f['height'] != 1080])
        self.assertEqual(result['requested_formats'][0]['format_id'], 'mp4-720')

    def test_progressive_needs_audio_and_respects_limit(self):
        self.assertEqual(self.select(self.formats(),True)['format_id'],'combined')

    def test_combined_1080_is_not_excluded_by_separate_streams(self):
        formats=self.formats()
        formats[-1].update(height=1080,width=1920,vbr=6000)
        result=self.select(formats)
        self.assertEqual(result['format_id'],'combined')


class AudioTests(unittest.TestCase):
    def test_failed_publication_survives_next_session_cleanup(self):
        app=VideoDownloader.__new__(VideoDownloader)
        app.file_lock=threading.Lock()
        app.log=Mock()
        app.record_problem=Mock()
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            pending=app.make_video_download_temp_dir(root,'https://example.invalid','id')
            pending.mkdir(parents=True)
            source=pending/'video.mkv'
            source.write_bytes(b'recovery data')
            from unittest.mock import patch
            with patch('src.downloads.files.shutil.move', side_effect=PermissionError('locked')):
                self.assertIsNone(app.finalize_downloaded_video_file(str(source),root,'id'))
            self.assertEqual(app.cleanup_stale_download_temp_dirs(root),0)
            self.assertEqual(source.read_bytes(),b'recovery data')
            self.assertNotEqual(pending,app.make_video_download_temp_dir(root,'https://example.invalid','id'))
    def test_duration_validation_both_directions_and_unknown(self):
        app = VideoDownloader.__new__(VideoDownloader)
        app.get_media_duration = Mock(return_value=10.)
        app.get_audio_duration = Mock(return_value=10.02)
        with tempfile.TemporaryDirectory() as d:
            source, output = Path(d)/'source.mkv', Path(d)/'out.mp3'
            output.write_bytes(b'x'*2000)
            self.assertTrue(app.validate_converted_audio(source,output)[0])
            for duration in (1.,30.,math.nan,math.inf):
                app.get_audio_duration.return_value=duration
                self.assertFalse(app.validate_converted_audio(source,output)[0])
            app.get_audio_duration.return_value=10.
            for duration in (0.,math.nan,math.inf):
                app.get_media_duration.return_value=duration
                self.assertFalse(app.validate_converted_audio(source,output)[0])

    def test_worker_uses_snapshot_without_touching_tk(self):
        app=SimpleNamespace(audio_quality=Mock(), download_settings_snapshot={'audio_quality':'320k'})
        app.audio_quality.get.side_effect=AssertionError('worker touched Tk')
        results=[]
        worker=threading.Thread(target=lambda: results.append(session_setting(app,'audio_quality')))
        worker.start(); worker.join(2)
        self.assertEqual(results,['320k'])
        app.audio_quality.get.assert_not_called()

    def test_real_ffmpeg_conversion(self):
        import shutil
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
            self.skipTest('FFmpeg and ffprobe required')
        app=VideoDownloader.__new__(VideoDownloader)
        app.cancel_flag=threading.Event()
        app.file_lock=threading.Lock()
        app.audio_quality=SimpleNamespace(get=lambda:'VBR-0 (лучшее)')
        app.subprocess_flags=0
        app.log=Mock()
        app.record_problem=Mock()
        app.run_command=lambda cmd, timeout, *args: subprocess.run(cmd,capture_output=True,text=True,timeout=timeout)
        with tempfile.TemporaryDirectory(prefix='audio_') as d:
            source=Path(d)/'тест видео.mkv'
            subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i','color=size=64x64:rate=10',
                            '-f','lavfi','-i','sine=frequency=880:sample_rate=48000','-t','2',
                            '-c:v','mpeg4','-c:a','pcm_s16le',str(source)],check=True,timeout=20)
            result=app.convert_videos_to_audio([str(source)],Path(d))
            self.assertEqual(result,[str(Path(d)/'тест видео.mp3')])
            self.assertTrue(source.exists())
            self.assertAlmostEqual(app.get_audio_duration(result[0]),2,delta=.1)
            decoded=subprocess.run(['ffmpeg','-v','error','-i',result[0],'-f','s16le','-ac','1','pipe:1'],
                                   capture_output=True,check=True,timeout=20).stdout
            import array
            samples=array.array('h',decoded)
            self.assertGreater(sum(x*x for x in samples)/len(samples),10000)
