"""Run: python tests/progressive_test.py (fully mocked; no Gemini quota)."""
import io
import json
import os
import sys
import tempfile
import time
import wave
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
os.environ['TRANSLATION_PROVIDER'] = 'gemini'
os.environ['TTS_PROVIDER'] = 'gemini'
import progressive


def synth_wav(client, text, path):
    with wave.open(str(path), 'wb') as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(24000)
        f.writeframes(b'\0\0' * 12000)  # half second, mock voice


def fake_synthesize(items, provider, client=None, voice='', max_parallel=4):
    for text, path in items:
        synth_wav(client, text, path)


class FakeInteractions:
    def create(self, *, model, input, response_format, **opts):
        clip = next(x for x in input if x['type'] == 'video')
        processing = clip['processing']
        assert processing['type'] == 'static'
        # The Interactions API expects duration strings such as "0s" / "20s".
        assert isinstance(processing['start_offset'], str)
        assert isinstance(processing['end_offset'], str)
        assert processing['start_offset'].endswith('s')
        assert processing['end_offset'].endswith('s')
        assert float(processing['end_offset'][:-1]) > float(processing['start_offset'][:-1])
        return SimpleNamespace(output_text=json.dumps({'segments': [
            {'start': 2., 'end': 5., 'original': 'Hi there', 'vi': 'Xin chao'}
        ]}))


class FakeClient:
    interactions = FakeInteractions()


def wait_for(predicate, timeout=5.):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate(): return True
        time.sleep(.05)
    return False


def main():
    original_client = progressive.gemini_client
    original_synth = progressive.synthesize_voices
    progressive.gemini_client = lambda *args, **kwargs: FakeClient()
    progressive.synthesize_voices = fake_synthesize
    try:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            job = progressive.ProgressiveJob('abcdef12345', 112., path)
            assert job.count == 8
            assert wait_for(lambda: job.snapshot()['completed_chunks'] == 4), job.snapshot()
            snap = job.snapshot()
            assert snap['stage'] == 'buffered', snap
            assert snap['playable_until'] == 60., snap
            assert set(snap['models']) == {'analysis', 'tts'}, snap
            assert snap['models']['analysis'], 'the analysis model in use should be reported'
            stats = snap['stats']
            assert stats['chunks_done'] == 4, stats
            assert stats['analysis_calls'] == 4, stats
            assert stats['tts_calls'] >= 4, stats
            assert stats['avg_chunk_seconds'] is not None, stats
            assert sorted(snap['ready_chunks']) == [0,1,2,3], snap
            assert not (path/'chunk_00004.wav').exists(), 'Worker should NOT process unneeded future'
            with wave.open(str(path/'chunk_00000.wav')) as wav:
                assert wav.getnframes() == 15*24000, wav.getnframes()
            job.seek(108)
            assert wait_for(lambda: 7 in job.snapshot()['ready_chunks']), job.snapshot()
            assert 4 not in job.snapshot()['ready_chunks'], 'Seek should jump to requested window'
            assert job.snapshot()['playhead_buffered_until'] == 112.
            job.seek(65)
            assert wait_for(lambda: job.snapshot()['stage'] == 'complete'), job.snapshot()
            job.close()
            job2 = progressive.ProgressiveJob('abcdef12345', 112., path)
            assert job2.snapshot()['stage'] == 'complete'
            assert job2.snapshot()['completed_chunks'] == 8
            job2.close()
            print('PASS: 15s WAV chunks, 60s initial buffer, on-demand seek, cache reuse')
        fake = FakeClient()
        cues = progressive.analyze_youtube_window(fake, 'abcdef12345', 112, 20, 40)
        assert cues and cues[0]['start'] >= 20
        print('PASS: Gemini YouTube clip API payload (mocked)')

        original_analyze = progressive.analyze_youtube_window
        original_attempts = progressive.CHUNK_ATTEMPTS
        original_delay = progressive.CHUNK_RETRY_SECONDS
        progressive.CHUNK_RETRY_SECONDS = 0
        calls = {'n': 0}
        def flaky(client, video_id, duration, begin, end):
            calls['n'] += 1
            if calls['n'] == 1:
                raise RuntimeError('503 high demand')
            return [{'start': begin + 1, 'end': begin + 2,
                     'original': 'hi', 'vi': 'Xin chào'}]
        try:
            progressive.analyze_youtube_window = flaky
            with tempfile.TemporaryDirectory() as folder:
                job = progressive.ProgressiveJob('retry123456', 40., Path(folder))
                assert wait_for(lambda: job.snapshot()['completed_chunks'] == 3), job.snapshot()
                assert calls['n'] >= 3, calls
                assert not job.snapshot()['error'], job.snapshot()
                job.close()
            print('PASS: one transient chunk failure is retried automatically')

            def broken(client, video_id, duration, begin, end):
                raise RuntimeError('quota exhausted')
            progressive.analyze_youtube_window = broken
            with tempfile.TemporaryDirectory() as folder:
                job = progressive.ProgressiveJob('broken123456', 20., Path(folder))
                assert wait_for(lambda: job.snapshot()['stage'] == 'error'), job.snapshot()
                assert 'quota exhausted' in job.snapshot()['error'], job.snapshot()
                job.close()
            print('PASS: retries stop after the configured attempts')
        finally:
            progressive.analyze_youtube_window = original_analyze
            progressive.CHUNK_ATTEMPTS = original_attempts
            progressive.CHUNK_RETRY_SECONDS = original_delay
    finally:
        progressive.gemini_client = original_client
        progressive.synthesize_voices = original_synth


if __name__ == '__main__':
    main()
