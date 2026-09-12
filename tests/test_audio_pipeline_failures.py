import tempfile
import threading
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from src.application import VideoDownloader
from src.core.errors import CommandCancelledError


class AudioPipelineFailures(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="audio ' тест ")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root/'input.mp3'
        self.source.write_bytes(b'a'*2000)
        app = self.app = VideoDownloader.__new__(VideoDownloader)
        app.log = Mock()
        app.record_problem = Mock()
        app.file_logger = Mock()
        app.file_lock = threading.Lock()
        app.cancel_flag = threading.Event()
        app.audio_quality = SimpleNamespace(get=lambda: '320k')
        app.split_hours = SimpleNamespace(get=lambda: 0)
        app.get_audio_duration = Mock(return_value=2.)
        app.get_total_audio_duration = Mock(return_value=2.)
        app.format_duration = str
        app.run_command = Mock(return_value=subprocess.CompletedProcess([],1,'','failure'))

    def test_merge_failure_is_not_swallowed(self):
        with self.assertRaises(RuntimeError):
            self.app.merge_audio_files([str(self.source)],str(self.root),'output')
        self.assertTrue(self.source.exists())

    def test_split_failure_preserves_source_and_old_files(self):
        old=self.root/'part_000.mp3'
        old.write_bytes(b'old')
        with self.assertRaises(RuntimeError):
            self.app.split_audio_file(self.source,self.root,'output',1)
        self.assertEqual(self.source.read_bytes(),b'a'*2000)
        self.assertEqual(old.read_bytes(),b'old')

    def test_zero_exit_without_parts_is_failure(self):
        self.app.run_command.return_value.returncode=0
        with self.assertRaises(RuntimeError):
            self.app.split_audio_file(self.source,self.root,'output',1)
        self.assertTrue(self.source.exists())

    def test_merge_rejects_missing_input(self):
        with self.assertRaises(FileNotFoundError):
            self.app.merge_audio_files([str(self.source),str(self.root/'missing.mp3')],str(self.root),'output')
        self.app.run_command.assert_not_called()

    def test_cancel_propagates(self):
        self.app.run_command.side_effect=CommandCancelledError('cancelled')
        with self.assertRaises(CommandCancelledError):
            self.app.merge_audio_files([str(self.source)],str(self.root),'output')

    def test_merge_duration_mismatch_is_failure(self):
        def run(cmd,*args):
            Path(cmd[-1]).write_bytes(b'a'*2000)
            return subprocess.CompletedProcess(cmd,0,'','')
        self.app.run_command.side_effect=run
        self.app.get_audio_duration.side_effect=lambda p: 2. if Path(p)==self.source else 20.
        with self.assertRaises(RuntimeError):
            self.app.merge_audio_files([str(self.source)],str(self.root),'output')

    def test_existing_output_is_preserved(self):
        out=self.root/'Разбивка аудио'
        out.mkdir()
        old=out/'output_Объединенное.mp3'
        old.write_bytes(b'old data')
        def run(cmd,*args):
            Path(cmd[-1]).write_bytes(b'new data'*300)
            return subprocess.CompletedProcess(cmd,0,'','')
        self.app.run_command.side_effect=run
        self.app.merge_audio_files([str(self.source)],str(self.root),'output')
        self.assertEqual(old.read_bytes(),b'old data')
        self.assertTrue((out/'output_Объединенное (2).mp3').exists())

    def test_conversion_does_not_reuse_unrelated_same_name_mp3(self):
        video=self.root/'video.mkv'
        video.write_bytes(b'video')
        old=self.root/'video.mp3'
        old.write_bytes(b'unrelated audio')
        self.app.get_media_duration=Mock(return_value=2.)
        self.app.has_audio_stream=Mock(return_value=True)
        self.app.validate_converted_audio=Mock(return_value=(True,'ok',2.,2.))
        def run(cmd,*args):
            Path(cmd[-1]).write_bytes(b'new audio')
            return subprocess.CompletedProcess(cmd,0,'','')
        self.app.run_command.side_effect=run
        result=self.app.convert_videos_to_audio([str(video)],self.root)
        self.assertEqual(result,[str(self.root/'video (2).mp3')])
        self.assertEqual(old.read_bytes(),b'unrelated audio')

    def test_second_part_publish_failure_then_retry_has_one_set(self):
        from unittest.mock import patch
        self.app.get_audio_duration.side_effect=lambda p: 2. if Path(p)==self.source else 1.
        def run(cmd,*args):
            pattern=cmd[-1]
            for i in range(2):
                Path(pattern % i).write_bytes(b'part'*500)
            return subprocess.CompletedProcess(cmd,0,'','')
        self.app.run_command.side_effect=run
        rename=Path.rename
        def fail_second(path,target):
            if path.name=='part_001.mp3':
                raise PermissionError('second rename failed')
            return rename(path,target)
        with patch.object(Path,'rename',fail_second):
            with self.assertRaises(PermissionError):
                self.app.split_audio_file(self.source,self.root,'out',1/3600)
        self.assertFalse(list(self.root.glob('out_Часть_*.mp3')))
        self.app.split_audio_file(self.source,self.root,'out',1/3600)
        self.assertEqual(sorted(p.name for p in self.root.glob('out_Часть_*.mp3')),
                         ['out_Часть_001.mp3','out_Часть_002.mp3'])
        self.assertTrue(self.source.exists())

    def test_real_merge_split_tone_order_and_paths(self):
        import shutil, array
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
            self.skipTest('FFmpeg/ffprobe required')
        app=VideoDownloader.__new__(VideoDownloader)
        app.log=Mock(); app.record_problem=Mock(); app.file_logger=Mock()
        app.file_lock=threading.Lock(); app.cancel_flag=threading.Event()
        app.subprocess_flags=0
        app.audio_quality=SimpleNamespace(get=lambda:'320k')
        app.split_hours=SimpleNamespace(get=lambda:0)
        app.run_command=lambda cmd,timeout,*args: subprocess.run(cmd,capture_output=True,text=True,timeout=timeout)
        inputs=[]
        for i,freq in enumerate((440,880)):
            path=self.root/f'тон {i}.mp3'
            subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i',
                            f'sine=frequency={freq}:sample_rate=48000','-t','1',
                            '-c:a','libmp3lame','-b:a','320k',str(path)],check=True,timeout=20)
            inputs.append(str(path))
        app.merge_audio_files(inputs,str(self.root),'tones')
        output_dir=self.root/'Разбивка аудио'
        merged=output_dir/'tones_Объединенное.mp3'
        self.assertTrue(merged.exists())
        app.split_audio_file(merged,output_dir,'tones',1/3600)
        parts=sorted(output_dir.glob('tones_Часть_*.mp3'))
        self.assertGreaterEqual(len(parts),2)
        self.assertTrue(merged.exists())
        def frequency(path,start):
            result=subprocess.run(['ffmpeg','-v','error','-ss',str(start),'-i',str(path),
                                   '-t','0.25','-ar','48000','-ac','1','-f','s16le','pipe:1'],
                                  capture_output=True,check=True,timeout=20)
            samples=array.array('h',result.stdout)
            crossings=sum(a<=0<b for a,b in zip(samples,samples[1:]))
            return crossings/(len(samples)/48000)
        self.assertAlmostEqual(frequency(merged,.2),440,delta=12)
        self.assertAlmostEqual(frequency(merged,1.2),880,delta=12)
        self.assertAlmostEqual(frequency(parts[0],.2),440,delta=12)
        self.assertAlmostEqual(frequency(parts[1],.2),880,delta=12)
