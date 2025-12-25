import os
import subprocess
import logging
import tempfile

logger = logging.getLogger(__name__)

def handler(event, context):
    """Test imageio_ffmpeg and test MP3 conversion"""
    result = {
        "imageio_ffmpeg_import": False,
        "ffmpeg_from_api": None,
        "ffmpeg_test": None,
        "conversion_test": None,
        "error": None
    }

    # 1. Test import
    try:
        import imageio_ffmpeg
        result['imageio_ffmpeg_import'] = True
    except ImportError as e:
        result['error'] = f"imageio_ffmpeg not installed: {e}"
        return result

    # 2. Get ffmpeg path directly from API
    try:
        ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
        result['ffmpeg_from_api'] = ffmpeg_path
        if ffmpeg_path:
            result['ffmpeg_path'] = ffmpeg_path

            # Test ffmpeg exists by running it
            version_result = subprocess.run(
                [ffmpeg_path, '-version'],
                capture_output=True, text=True, timeout=5
            )
            if version_result.returncode == 0:
                result['ffmpeg_test'] = version_result.stdout.split('\n')[0]

                # 3. Test MP3 conversion (create a small test video first)
                with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as test_video:
                    test_video_path = test_video.name
                    test_mp3_path = test_video_path.replace('.mp4', '.mp3')

                    # Generate a simple test video (1 second, black screen, silent audio)
                    # Using lavfi to generate input
                    cmd = [
                        ffmpeg_path,
                        '-f', 'lavfi',
                        '-i', 'color=c=black:s=320x240:d=1',
                        '-f', 'lavfi',
                        '-i', 'sine=frequency=1000:duration=1',
                        '-c:v', 'libx264',
                        '-c:a', 'aac',
                        '-y',
                        test_video_path
                    ]

                    gen_result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

                    if gen_result.returncode != 0:
                        result['conversion_test'] = f"Video gen failed: {gen_result.stderr[:200]}"
                    else:
                        # Now test MP3 conversion
                        convert_cmd = [
                            ffmpeg_path,
                            '-i', test_video_path,
                            '-vn',
                            '-acodec', 'libmp3lame',
                            '-ab', '192k',
                            '-y',
                            test_mp3_path
                        ]

                        convert_result = subprocess.run(convert_cmd, capture_output=True, text=True, timeout=30)

                        if convert_result.returncode == 0 and os.path.exists(test_mp3_path):
                            file_size = os.path.getsize(test_mp3_path)
                            result['conversion_test'] = f"Success! Created MP3: {file_size} bytes"
                        else:
                            result['conversion_test'] = f"Convert failed: {convert_result.stderr[:200]}"

                    # Cleanup
                    try:
                        os.unlink(test_video_path)
                        if os.path.exists(test_mp3_path):
                            os.unlink(test_mp3_path)
                    except:
                        pass
            else:
                result['ffmpeg_test'] = f"FFmpeg failed: {version_result.stderr[:200]}"

    except Exception as e:
        result['error'] = f"Error: {str(e)}"

    return result
