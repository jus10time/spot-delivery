import base64
import json
import os
import re
import shlex
import shutil
import subprocess
import time
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Tuple
from uuid import uuid4

from flask import Flask, abort, jsonify, render_template, request, send_file
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
PROFILES_PATH = BASE_DIR / 'config' / 'profiles.json'
DEFAULT_OUTPUT_DIR = BASE_DIR / 'outputs'
UPLOADS_DIR = BASE_DIR / 'uploads'
SOURCE_UPLOADS_DIR = UPLOADS_DIR / 'source'
SLATE_UPLOADS_DIR = UPLOADS_DIR / 'slate'
PROFILE_KEY_RE = re.compile(r'[^a-z0-9_]+')
IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.bmp', '.tif', '.tiff', '.webp'}
DOWNLOAD_TOKEN_TTL_SECONDS = 24 * 60 * 60
DOWNLOAD_TOKENS: Dict[str, Dict[str, Any]] = {}
RETENTION_DAYS_DEFAULT = 14
RETENTION_CLEANUP_INTERVAL_SECONDS = 60 * 60
LAST_RETENTION_CLEANUP_AT = 0.0

app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0


@app.after_request
def add_no_cache_headers(response: Any) -> Any:
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


def load_profiles() -> Dict[str, Any]:
    with PROFILES_PATH.open('r', encoding='utf-8') as f:
        data = json.load(f)
    return data.get('profiles', {})


def save_profiles(profiles: Dict[str, Any]) -> None:
    payload = {'profiles': profiles}
    with PROFILES_PATH.open('w', encoding='utf-8') as f:
        json.dump(payload, f, indent=2)
        f.write('\n')


def normalize_profile_key(value: Any) -> str:
    raw = str(value or '').strip().lower()
    key = PROFILE_KEY_RE.sub('_', raw).strip('_')
    return key


def sanitize_uploaded_filename(filename: str, fallback: str) -> str:
    cleaned = secure_filename(filename or '')
    return cleaned or fallback


def save_uploaded_file(
    file_obj: Any,
    target_dir: Path,
    prefix: str,
    allowed_extensions: set[str] | None = None,
) -> Path:
    if not file_obj or not getattr(file_obj, 'filename', ''):
        raise RuntimeError('No file selected.')

    original_name = sanitize_uploaded_filename(file_obj.filename, f'{prefix}.bin')
    extension = Path(original_name).suffix.lower()
    if allowed_extensions is not None and extension not in allowed_extensions:
        allowed = ', '.join(sorted(allowed_extensions))
        raise RuntimeError(f'Unsupported file type. Allowed: {allowed}')

    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_path = (target_dir / f'{prefix}_{timestamp}_{original_name}').resolve()
    file_obj.save(output_path)
    return output_path


def _cleanup_download_tokens(now: float | None = None) -> None:
    current_time = now if now is not None else time.time()
    expired = [token for token, meta in DOWNLOAD_TOKENS.items() if meta.get('expires_at', 0) <= current_time]
    for token in expired:
        DOWNLOAD_TOKENS.pop(token, None)


def register_download_token(file_path: Path) -> str:
    _cleanup_download_tokens()
    token = uuid4().hex
    DOWNLOAD_TOKENS[token] = {
        'path': str(file_path.resolve()),
        'expires_at': time.time() + DOWNLOAD_TOKEN_TTL_SECONDS,
    }
    return token


def resolve_download_token(token: str) -> Path | None:
    _cleanup_download_tokens()
    meta = DOWNLOAD_TOKENS.get(token)
    if not meta:
        return None
    path_raw = meta.get('path')
    if not path_raw:
        return None
    path = Path(path_raw).resolve()
    if not path.exists() or not path.is_file():
        return None
    return path


def retention_days() -> int:
    raw = str(os.getenv('SPOT_DELIVERY_RETENTION_DAYS', RETENTION_DAYS_DEFAULT)).strip()
    try:
        parsed = int(raw)
        return parsed if parsed > 0 else RETENTION_DAYS_DEFAULT
    except (TypeError, ValueError):
        return RETENTION_DAYS_DEFAULT


def cleanup_old_media_files(now: float | None = None) -> int:
    current_time = now if now is not None else time.time()
    cutoff = current_time - (retention_days() * 24 * 60 * 60)
    targets = [SOURCE_UPLOADS_DIR, SLATE_UPLOADS_DIR, DEFAULT_OUTPUT_DIR]
    deleted = 0

    for target_dir in targets:
        if not target_dir.exists():
            continue
        for path in target_dir.rglob('*'):
            if not path.is_file():
                continue
            if path.name == '.gitkeep':
                continue
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    deleted += 1
            except OSError:
                continue
    return deleted


def maybe_run_retention_cleanup(force: bool = False) -> int:
    global LAST_RETENTION_CLEANUP_AT
    now = time.time()
    if not force and (now - LAST_RETENTION_CLEANUP_AT) < RETENTION_CLEANUP_INTERVAL_SECONDS:
        return 0
    deleted = cleanup_old_media_files(now=now)
    LAST_RETENTION_CLEANUP_AT = now
    return deleted


def list_available_slates() -> List[Dict[str, Any]]:
    SLATE_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    items: List[Dict[str, Any]] = []
    for path in sorted(SLATE_UPLOADS_DIR.glob('*')):
        if not path.is_file():
            continue
        if path.name == '.gitkeep':
            continue
        if path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        stat = path.stat()
        items.append(
            {
                'name': path.name,
                'path': str(path.resolve()),
                'size_bytes': stat.st_size,
                'modified_at': datetime.fromtimestamp(stat.st_mtime).isoformat(timespec='seconds'),
            }
        )
    return items


def resolve_slate_library_file(raw_path: str) -> Path | None:
    try:
        candidate = Path(raw_path).expanduser().resolve()
    except (TypeError, ValueError, OSError):
        return None
    base = SLATE_UPLOADS_DIR.resolve()
    if candidate == base or base not in candidate.parents:
        return None
    if not candidate.is_file():
        return None
    if candidate.suffix.lower() not in IMAGE_EXTENSIONS:
        return None
    return candidate


def ffmpeg_escape(text: str) -> str:
    return (
        text.replace('\\', '\\\\')
        .replace(':', '\\:')
        .replace("'", "\\'")
        .replace('%', '\\%')
        .replace(',', '\\,')
    )


@lru_cache(maxsize=1)
def resolve_ffmpeg_tools() -> Tuple[str, str]:
    env_ffmpeg = os.getenv('SPOT_DELIVERY_FFMPEG_BIN')
    env_ffprobe = os.getenv('SPOT_DELIVERY_FFPROBE_BIN')

    candidates: List[Tuple[str, str]] = []

    def add_candidate(ffmpeg_bin: str | None, ffprobe_bin: str | None) -> None:
        if ffmpeg_bin and ffprobe_bin:
            candidates.append((ffmpeg_bin, ffprobe_bin))

    if env_ffmpeg:
        inferred_ffprobe = env_ffprobe or str(Path(env_ffmpeg).expanduser().with_name('ffprobe'))
        add_candidate(env_ffmpeg, inferred_ffprobe)

    add_candidate('/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg', '/opt/homebrew/opt/ffmpeg-full/bin/ffprobe')
    add_candidate('/usr/local/opt/ffmpeg-full/bin/ffmpeg', '/usr/local/opt/ffmpeg-full/bin/ffprobe')
    add_candidate(shutil.which('ffmpeg'), shutil.which('ffprobe'))

    for ffmpeg_bin, ffprobe_bin in candidates:
        ffmpeg_path = Path(ffmpeg_bin).expanduser()
        ffprobe_path = Path(ffprobe_bin).expanduser()
        if not ffmpeg_path.exists() or not ffprobe_path.exists():
            continue
        try:
            ffmpeg_check = subprocess.run(
                [str(ffmpeg_path), '-hide_banner', '-version'],
                capture_output=True,
                text=True,
            )
            ffprobe_check = subprocess.run(
                [str(ffprobe_path), '-hide_banner', '-version'],
                capture_output=True,
                text=True,
            )
        except OSError:
            continue

        if ffmpeg_check.returncode == 0 and ffprobe_check.returncode == 0:
            return str(ffmpeg_path), str(ffprobe_path)

    raise RuntimeError(
        'Unable to find a working ffmpeg/ffprobe pair. '
        'Install ffmpeg-full or set SPOT_DELIVERY_FFMPEG_BIN and SPOT_DELIVERY_FFPROBE_BIN.'
    )


def probe_media(input_path: Path) -> Dict[str, Any]:
    _, ffprobe_bin = resolve_ffmpeg_tools()
    cmd = [
        ffprobe_bin,
        '-v',
        'error',
        '-show_entries',
        'format=duration:stream=index,codec_type,avg_frame_rate,r_frame_rate',
        '-of',
        'json',
        str(input_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(result.stdout)
    streams = data.get('streams', [])

    duration = float(data.get('format', {}).get('duration', 0.0) or 0.0)
    has_audio = any(s.get('codec_type') == 'audio' for s in streams)
    video_stream = next((s for s in streams if s.get('codec_type') == 'video'), {})
    source_fps = (
        normalize_fps_value(video_stream.get('avg_frame_rate'), '')
        or normalize_fps_value(video_stream.get('r_frame_rate'), '')
        or None
    )

    if duration <= 0:
        raise RuntimeError('Unable to read input duration from ffprobe.')

    return {'duration': duration, 'has_audio': has_audio, 'source_fps': source_fps}


def ensure_ffmpeg_tools() -> None:
    resolve_ffmpeg_tools()


@lru_cache(maxsize=1)
def ffmpeg_supports_drawtext() -> bool:
    ffmpeg_bin, _ = resolve_ffmpeg_tools()
    filters = subprocess.run([ffmpeg_bin, '-hide_banner', '-filters'], capture_output=True, text=True)
    if filters.returncode != 0:
        return False
    return ' drawtext ' in filters.stdout


def normalize_positive_number(value: Any, fallback: float) -> float:
    try:
        parsed = float(value)
        if parsed <= 0:
            return fallback
        return parsed
    except (TypeError, ValueError):
        return fallback


def normalize_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def normalize_fps_value(value: Any, fallback: str = '30000/1001') -> str:
    text = str(value or '').strip()
    if not text:
        return fallback
    if '/' in text:
        try:
            num_raw, den_raw = text.split('/', 1)
            numerator = float(num_raw)
            denominator = float(den_raw)
            if numerator > 0 and denominator > 0:
                return text
        except (TypeError, ValueError, ZeroDivisionError):
            return fallback
        return fallback
    try:
        numeric = float(text)
        if numeric > 0:
            return text
    except (TypeError, ValueError):
        return fallback
    return fallback


def normalize_bool(value: Any, fallback: bool = False) -> bool:
    if value is None:
        return fallback
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0

    text = str(value).strip().lower()
    if text in {'1', 'true', 'yes', 'y', 'on'}:
        return True
    if text in {'0', 'false', 'no', 'n', 'off', ''}:
        return False
    return fallback


def infer_output_extension(video_codec: str) -> str:
    codec = str(video_codec or '').strip().lower()
    if codec == 'mpeg2video':
        return 'mpg'
    if codec in {'libx264', 'libx265'}:
        return 'mp4'
    if codec in {'dnxhd', 'dnxhr'}:
        return 'mov'
    return 'mov'


def normalize_output_extension(value: Any, video_codec: str, fallback: str = 'mov') -> str:
    raw = str(value or '').strip().lower().lstrip('.')
    if not raw:
        inferred = infer_output_extension(video_codec)
        return inferred or fallback

    safe = ''.join(ch for ch in raw if ch.isalnum())
    if not safe:
        inferred = infer_output_extension(video_codec)
        return inferred or fallback
    return safe


def normalize_resolution(value: Any, fallback: str = '1920x1080') -> Tuple[str, str]:
    text = str(value or fallback).strip().lower()
    separator = 'x' if 'x' in text else ':' if ':' in text else None
    if separator:
        width_raw, height_raw = text.split(separator, 1)
        try:
            width = int(width_raw)
            height = int(height_raw)
            if width > 0 and height > 0:
                return f'{width}x{height}', f'{width}:{height}'
        except ValueError:
            pass

    fallback_text = fallback.strip().lower()
    fallback_separator = 'x' if 'x' in fallback_text else ':'
    fw_raw, fh_raw = fallback_text.split(fallback_separator, 1)
    fw = int(fw_raw)
    fh = int(fh_raw)
    return f'{fw}x{fh}', f'{fw}:{fh}'


def build_slate_drawtext_ops(
    profile: Dict[str, Any],
    slate: Dict[str, str],
    slate_layout: Dict[str, Any],
) -> List[str]:
    left_x = normalize_int(
        slate_layout.get('left_x'),
        normalize_int(profile.get('slate_left_x', 220), 220),
    )
    top_y = normalize_int(
        slate_layout.get('top_y'),
        normalize_int(profile.get('slate_top_y', 320), 320),
    )
    line_gap = normalize_int(
        slate_layout.get('line_gap'),
        normalize_int(profile.get('slate_line_gap', 72), 72),
    )
    header_y = normalize_int(
        slate_layout.get('header_y'),
        normalize_int(profile.get('slate_header_y', 120), 120),
    )
    font_size = normalize_int(
        slate_layout.get('font_size'),
        normalize_int(profile.get('slate_font_size', 52), 52),
    )
    header_size = normalize_int(
        slate_layout.get('header_size'),
        normalize_int(profile.get('slate_header_size', 70), 70),
    )

    header = ffmpeg_escape(slate.get('client', '').strip() or 'CLIENT')
    lines = [
        ('Name', slate.get('name', '').strip()),
        ('ISCI code', slate.get('isci', '').strip()),
        ('Job#', slate.get('job_number', '').strip()),
        ('Date', slate.get('date', '').strip()),
        ('Length', slate.get('length', '').strip()),
        ('Audio', slate.get('audio', '').strip()),
    ]
    draw_ops = [
        (
            "drawtext=fontcolor=white:fontsize={size}:x=(w-text_w)/2:y={y}:"
            "text='{text}'"
        ).format(size=header_size, y=header_y, text=header)
    ]
    for idx, (label, value) in enumerate(lines):
        y = top_y + (idx * line_gap)
        text = ffmpeg_escape(f'{label}: {value or "-"}')
        draw_ops.append(
            (
                "drawtext=fontcolor=white:fontsize={size}:x={x}:y={y}:"
                "text='{text}'"
            ).format(size=font_size, x=left_x, y=y, text=text)
        )
    return draw_ops


def build_profile_payload(payload: Dict[str, Any], existing: Dict[str, Any] | None = None) -> Dict[str, Any]:
    current = dict(existing or {})
    resolution_input = payload.get('resolution', current.get('resolution', '1920x1080'))
    resolution, _ = normalize_resolution(resolution_input, '1920x1080')

    current['label'] = str(payload.get('label', current.get('label', ''))).strip() or 'New Profile'
    current['resolution'] = resolution
    current['fps'] = normalize_fps_value(
        payload.get('fps', current.get('fps', '30000/1001')),
        '30000/1001',
    )
    current['keep_frame_rate'] = normalize_bool(
        payload.get('keep_frame_rate', current.get('keep_frame_rate', False)),
        False,
    )
    current['black_lead_sec'] = normalize_positive_number(
        payload.get('black_lead_sec', current.get('black_lead_sec', 1)),
        1,
    )
    current['slate_sec'] = normalize_positive_number(
        payload.get('slate_sec', current.get('slate_sec', 5)),
        5,
    )
    current['black_pre_spot_sec'] = normalize_positive_number(
        payload.get('black_pre_spot_sec', current.get('black_pre_spot_sec', 2)),
        2,
    )
    current['black_tail_sec'] = normalize_positive_number(
        payload.get('black_tail_sec', current.get('black_tail_sec', 1)),
        1,
    )
    current['video_codec'] = (
        str(payload.get('video_codec', current.get('video_codec', 'prores_ks'))).strip()
        or 'prores_ks'
    )
    current['output_extension'] = normalize_output_extension(
        payload.get('output_extension', current.get('output_extension', '')),
        current['video_codec'],
        infer_output_extension(current['video_codec']),
    )
    current['prores_profile'] = normalize_int(
        payload.get('prores_profile', current.get('prores_profile', 2)),
        2,
    )
    current['pixel_format'] = (
        str(payload.get('pixel_format', current.get('pixel_format', 'yuv422p10le'))).strip()
        or 'yuv422p10le'
    )
    current['audio_codec'] = (
        str(payload.get('audio_codec', current.get('audio_codec', 'pcm_s24le'))).strip()
        or 'pcm_s24le'
    )
    current['audio_rate'] = max(
        1,
        normalize_int(payload.get('audio_rate', current.get('audio_rate', 48000)), 48000),
    )
    return current


def build_filter_complex(
    profile: Dict[str, Any],
    slate: Dict[str, str],
    slate_layout: Dict[str, Any],
    fps: str,
    spot_duration: float,
    has_audio: bool,
    include_slate_text: bool,
) -> Tuple[str, float]:
    resolution, pad_resolution = normalize_resolution(profile.get('resolution', '1920x1080'))

    black_1 = normalize_positive_number(profile.get('black_lead_sec', 1), 1)
    slate_sec = normalize_positive_number(profile.get('slate_sec', 5), 5)
    black_2 = normalize_positive_number(profile.get('black_pre_spot_sec', 2), 2)
    black_3 = normalize_positive_number(profile.get('black_tail_sec', 1), 1)

    pre_roll = black_1 + slate_sec + black_2
    total_duration = pre_roll + spot_duration + black_3

    slate_base_chain = (
        '[1:v]scale={res}:force_original_aspect_ratio=decrease,'
        'pad={pad_res}:(ow-iw)/2:(oh-ih)/2:black,fps={fps},setsar=1,'
        'trim=duration={dur},setpts=PTS-STARTPTS[slatebase]'
    ).format(res=resolution, pad_res=pad_resolution, fps=fps, dur=f'{slate_sec:.3f}')

    if include_slate_text:
        draw_ops = build_slate_drawtext_ops(
            profile=profile,
            slate=slate,
            slate_layout=slate_layout,
        )
        slate_chain = '[slatebase]' + ','.join(draw_ops) + '[slatev]'
    else:
        slate_chain = '[slatebase]null[slatev]'

    spot_video_chain = (
        '[3:v]scale={res}:force_original_aspect_ratio=decrease,'
        'pad={pad_res}:(ow-iw)/2:(oh-ih)/2:black,fps={fps},setsar=1,'
        'trim=duration={dur},setpts=PTS-STARTPTS[spotv]'
    ).format(res=resolution, pad_res=pad_resolution, fps=fps, dur=f'{spot_duration:.3f}')

    video_concat = '[0:v][slatev][2:v][spotv][4:v]concat=n=5:v=1:a=0[vout]'

    if has_audio:
        audio_chain = (
            '[3:a]aresample=48000,aformat=channel_layouts=stereo,'
            'atrim=duration={spot},asetpts=PTS-STARTPTS,'
            'adelay={delay}|{delay},apad=pad_dur={tail},'
            'atrim=duration={total}[aout]'
        ).format(
            spot=f'{spot_duration:.3f}',
            delay=int(pre_roll * 1000),
            tail=f'{black_3:.3f}',
            total=f'{total_duration:.3f}',
        )
    else:
        audio_chain = (
            'anullsrc=r=48000:cl=stereo,atrim=duration={total}[aout]'
        ).format(total=f'{total_duration:.3f}')

    filter_complex = ';'.join([slate_base_chain, slate_chain, spot_video_chain, video_concat, audio_chain])
    return filter_complex, total_duration


def run_profile_render(
    input_path: Path,
    output_dir: Path,
    output_filename_stem: str,
    profile_key: str,
    profile: Dict[str, Any],
    slate: Dict[str, str],
    slate_layout: Dict[str, Any],
    slate_background_image: Path | None,
    requested_spot_duration: float,
) -> Dict[str, Any]:
    ffmpeg_bin, _ = resolve_ffmpeg_tools()
    probe = probe_media(input_path)
    source_duration = probe['duration']
    has_audio = probe['has_audio']
    source_fps = normalize_fps_value(probe.get('source_fps'), '')

    spot_duration = min(requested_spot_duration, source_duration)
    if spot_duration <= 0:
        raise RuntimeError('Spot duration resolved to 0 seconds.')

    output_dir.mkdir(parents=True, exist_ok=True)
    output_extension = normalize_output_extension(
        profile.get('output_extension', ''),
        str(profile.get('video_codec', 'prores_ks')),
        infer_output_extension(str(profile.get('video_codec', 'prores_ks'))),
    )
    output_path = output_dir / f'{output_filename_stem}.{output_extension}'

    resolution, _ = normalize_resolution(profile.get('resolution', '1920x1080'))
    configured_fps = normalize_fps_value(profile.get('fps', '30000/1001'), '30000/1001')
    keep_frame_rate = normalize_bool(profile.get('keep_frame_rate', False), False)
    fps = source_fps if keep_frame_rate and source_fps else configured_fps
    black_1 = normalize_positive_number(profile.get('black_lead_sec', 1), 1)
    slate_sec = normalize_positive_number(profile.get('slate_sec', 5), 5)
    black_2 = normalize_positive_number(profile.get('black_pre_spot_sec', 2), 2)
    black_3 = normalize_positive_number(profile.get('black_tail_sec', 1), 1)

    include_slate_text = ffmpeg_supports_drawtext()
    filter_complex, total_duration = build_filter_complex(
        profile=profile,
        slate=slate,
        slate_layout=slate_layout,
        fps=fps,
        spot_duration=spot_duration,
        has_audio=has_audio,
        include_slate_text=include_slate_text,
    )

    cmd = [
        ffmpeg_bin,
        '-y',
        '-f',
        'lavfi',
        '-t',
        f'{black_1:.3f}',
        '-i',
        f'color=c=black:s={resolution}:r={fps}',
    ]
    if slate_background_image:
        cmd.extend([
            '-loop',
            '1',
            '-t',
            f'{slate_sec:.3f}',
            '-i',
            str(slate_background_image),
        ])
    else:
        cmd.extend([
            '-f',
            'lavfi',
            '-t',
            f'{slate_sec:.3f}',
            '-i',
            f'color=c=black:s={resolution}:r={fps}',
        ])

    cmd.extend([
        '-f',
        'lavfi',
        '-t',
        f'{black_2:.3f}',
        '-i',
        f'color=c=black:s={resolution}:r={fps}',
        '-i',
        str(input_path),
        '-f',
        'lavfi',
        '-t',
        f'{black_3:.3f}',
        '-i',
        f'color=c=black:s={resolution}:r={fps}',
        '-filter_complex',
        filter_complex,
        '-map',
        '[vout]',
        '-map',
        '[aout]',
        '-c:v',
        profile.get('video_codec', 'prores_ks'),
        '-profile:v',
        str(profile.get('prores_profile', 2)),
        '-pix_fmt',
        profile.get('pixel_format', 'yuv422p10le'),
        '-c:a',
        profile.get('audio_codec', 'pcm_s24le'),
        '-ar',
        str(profile.get('audio_rate', 48000)),
        '-movflags',
        '+faststart',
        str(output_path),
    ])

    process = subprocess.run(cmd, capture_output=True, text=True)
    if process.returncode != 0:
        err = process.stderr[-2500:] if process.stderr else 'Unknown ffmpeg error.'
        raise RuntimeError(err)

    result = {
        'profile': profile_key,
        'output_path': str(output_path),
        'output_filename': output_path.name,
        'spot_duration_sec': round(spot_duration, 3),
        'total_duration_sec': round(total_duration, 3),
        'has_source_audio': has_audio,
        'frame_rate': fps,
        'keep_frame_rate': keep_frame_rate,
        'slate_background_image': str(slate_background_image) if slate_background_image else None,
        'command': ' '.join(shlex.quote(part) for part in cmd),
    }
    if not include_slate_text:
        result['warning'] = (
            "FFmpeg 'drawtext' filter is unavailable. Slate text was skipped for this render."
        )
    return result


@app.get('/')
def home() -> Any:
    profiles = load_profiles()
    return render_template(
        'index.html',
        profiles=profiles,
        drawtext_supported=ffmpeg_supports_drawtext(),
        browse_supported=bool(shutil.which('osascript')),
    )


@app.get('/profiles')
def profiles_page() -> Any:
    return render_template('profiles.html', profiles=load_profiles())


@app.get('/slates')
def slates_page() -> Any:
    return render_template('slates.html')


@app.get('/api/profiles')
def profiles_api() -> Any:
    return jsonify(load_profiles())


@app.get('/api/slates')
def slates_api() -> Any:
    maybe_run_retention_cleanup()
    return jsonify({'ok': True, 'slates': list_available_slates()})


@app.post('/api/profiles/save')
def profiles_save_api() -> Any:
    try:
        payload = request.get_json(force=True) or {}
        key = normalize_profile_key(payload.get('key'))
        if not key:
            return jsonify({'error': 'Profile key is required (letters, numbers, underscore).'}), 400

        source_key = normalize_profile_key(payload.get('source_key'))
        profiles = load_profiles()
        existing = profiles.get(key, {})
        updated = build_profile_payload(payload, existing=existing)
        if not str(updated.get('label', '')).strip():
            updated['label'] = key
        profiles[key] = updated

        if source_key and source_key != key and source_key in profiles:
            del profiles[source_key]

        save_profiles(profiles)
        return jsonify({'ok': True, 'key': key, 'profile': updated, 'profiles': profiles})
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


@app.post('/api/profiles/delete')
def profiles_delete_api() -> Any:
    try:
        payload = request.get_json(force=True) or {}
        key = normalize_profile_key(payload.get('key'))
        if not key:
            return jsonify({'error': 'Profile key is required.'}), 400

        profiles = load_profiles()
        if key not in profiles:
            return jsonify({'error': f'Profile not found: {key}'}), 404
        if len(profiles) <= 1:
            return jsonify({'error': 'At least one profile must remain.'}), 400

        del profiles[key]
        save_profiles(profiles)
        return jsonify({'ok': True, 'deleted': key, 'profiles': profiles})
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


@app.post('/api/preview-slate')
def preview_slate_api() -> Any:
    try:
        ensure_ffmpeg_tools()
        ffmpeg_bin, _ = resolve_ffmpeg_tools()

        payload = request.get_json(force=True) or {}
        profile_key = normalize_profile_key(payload.get('profile_key'))
        if not profile_key:
            return jsonify({'error': 'Profile key is required for slate preview.'}), 400

        profiles = load_profiles()
        profile = profiles.get(profile_key)
        if not profile:
            return jsonify({'error': f'Unknown profile: {profile_key}'}), 400

        slate = payload.get('slate') or {}
        slate_layout = payload.get('slate_layout') or {}
        slate_background_image_raw = (payload.get('slate_background_image') or '').strip()
        slate_background_image: Path | None = None
        if slate_background_image_raw:
            slate_background_image = Path(slate_background_image_raw).expanduser().resolve()
            if not slate_background_image.exists():
                return jsonify(
                    {'error': f'Slate background image not found: {slate_background_image}'}
                ), 400

        resolution, pad_resolution = normalize_resolution(profile.get('resolution', '1920x1080'))
        fps = str(profile.get('fps', '30000/1001'))
        include_slate_text = ffmpeg_supports_drawtext()

        cmd: List[str] = [ffmpeg_bin, '-y']
        if slate_background_image:
            cmd.extend(['-loop', '1', '-t', '1', '-i', str(slate_background_image)])
        else:
            cmd.extend(['-f', 'lavfi', '-t', '1', '-i', f'color=c=black:s={resolution}:r={fps}'])

        vf_parts = [
            f'scale={resolution}:force_original_aspect_ratio=decrease',
            f'pad={pad_resolution}:(ow-iw)/2:(oh-ih)/2:black',
            f'fps={fps}',
            'setsar=1',
        ]
        if include_slate_text:
            vf_parts.extend(build_slate_drawtext_ops(profile=profile, slate=slate, slate_layout=slate_layout))

        cmd.extend(
            [
                '-vf',
                ','.join(vf_parts),
                '-frames:v',
                '1',
                '-f',
                'image2pipe',
                '-vcodec',
                'png',
                'pipe:1',
            ]
        )

        process = subprocess.run(cmd, capture_output=True)
        if process.returncode != 0 or not process.stdout:
            stderr_text = (process.stderr or b'').decode('utf-8', errors='ignore')
            err = stderr_text[-2500:] if stderr_text else 'Unknown ffmpeg preview error.'
            raise RuntimeError(err)

        image_base64 = base64.b64encode(process.stdout).decode('ascii')
        response: Dict[str, Any] = {
            'ok': True,
            'profile': profile_key,
            'image_data_url': f'data:image/png;base64,{image_base64}',
        }
        if not include_slate_text:
            response['warning'] = (
                "FFmpeg 'drawtext' filter is unavailable. Slate text was skipped for preview."
            )
        return jsonify(response)
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


@app.get('/api/system-check')
def system_check_api() -> Any:
    path_ffmpeg = shutil.which('ffmpeg')
    path_ffprobe = shutil.which('ffprobe')
    browse_supported = bool(shutil.which('osascript'))
    try:
        selected_ffmpeg, selected_ffprobe = resolve_ffmpeg_tools()
        return jsonify(
            {
                'ok': True,
                'ffmpeg_path': path_ffmpeg,
                'ffprobe_path': path_ffprobe,
                'selected_ffmpeg': selected_ffmpeg,
                'selected_ffprobe': selected_ffprobe,
                'drawtext_supported': ffmpeg_supports_drawtext(),
                'browse_supported': browse_supported,
                'retention_days': retention_days(),
            }
        )
    except Exception as exc:
        return jsonify(
            {
                'ok': False,
                'ffmpeg_path': path_ffmpeg,
                'ffprobe_path': path_ffprobe,
                'selected_ffmpeg': None,
                'selected_ffprobe': None,
                'drawtext_supported': False,
                'browse_supported': browse_supported,
                'retention_days': retention_days(),
                'error': str(exc),
            }
        )


@app.post('/api/upload-source')
def upload_source_api() -> Any:
    try:
        maybe_run_retention_cleanup()
        uploaded = request.files.get('file')
        saved_path = save_uploaded_file(
            file_obj=uploaded,
            target_dir=SOURCE_UPLOADS_DIR,
            prefix='source',
        )
        return jsonify({'ok': True, 'path': str(saved_path)})
    except Exception as exc:
        return jsonify({'error': str(exc)}), 400


@app.post('/api/upload-slate-background')
def upload_slate_background_api() -> Any:
    try:
        maybe_run_retention_cleanup()
        uploaded = request.files.get('file')
        saved_path = save_uploaded_file(
            file_obj=uploaded,
            target_dir=SLATE_UPLOADS_DIR,
            prefix='slate_bg',
            allowed_extensions=IMAGE_EXTENSIONS,
        )
        return jsonify({'ok': True, 'path': str(saved_path), 'slates': list_available_slates()})
    except Exception as exc:
        return jsonify({'error': str(exc)}), 400


@app.post('/api/slates/delete')
def delete_slate_api() -> Any:
    try:
        payload = request.get_json(force=True) or {}
        slate_path_raw = str(payload.get('path') or '').strip()
        if not slate_path_raw:
            return jsonify({'error': 'Slate path is required.'}), 400

        slate_path = resolve_slate_library_file(slate_path_raw)
        if slate_path is None:
            return jsonify({'error': 'Slate not found in slate library.'}), 404

        slate_path.unlink(missing_ok=False)
        return jsonify({'ok': True, 'deleted': str(slate_path), 'slates': list_available_slates()})
    except Exception as exc:
        return jsonify({'error': str(exc)}), 400


@app.get('/api/download/<token>')
def download_output_api(token: str) -> Any:
    file_path = resolve_download_token(token)
    if file_path is None:
        abort(404)
    return send_file(
        str(file_path),
        as_attachment=True,
        download_name=file_path.name,
        mimetype='video/quicktime',
    )


def _run_osascript(script: str) -> str:
    if not shutil.which('osascript'):
        raise RuntimeError(
            'Native file picker is unavailable on this server. '
            'Enter the full path manually in the field.'
        )
    result = subprocess.run(['osascript', '-e', script], capture_output=True, text=True)
    if result.returncode != 0:
        stderr = (result.stderr or '').strip()
        if 'User canceled' in stderr:
            raise RuntimeError('Selection cancelled.')
        raise RuntimeError(stderr or 'Unable to open file browser dialog.')
    return (result.stdout or '').strip()


@app.post('/api/browse-source')
def browse_source_api() -> Any:
    try:
        selected = _run_osascript(
            'POSIX path of (choose file with prompt "Select source video file")'
        )
        return jsonify({'ok': True, 'path': selected})
    except Exception as exc:
        return jsonify({'error': str(exc)}), 400


@app.post('/api/browse-destination')
def browse_destination_api() -> Any:
    try:
        selected = _run_osascript(
            'POSIX path of (choose folder with prompt "Select destination folder")'
        )
        return jsonify({'ok': True, 'path': selected})
    except Exception as exc:
        return jsonify({'error': str(exc)}), 400


@app.post('/api/browse-slate-background')
def browse_slate_background_api() -> Any:
    try:
        selected = _run_osascript(
            'POSIX path of (choose file with prompt "Select slate background image")'
        )
        return jsonify({'ok': True, 'path': selected})
    except Exception as exc:
        return jsonify({'error': str(exc)}), 400


@app.post('/api/render')
def render_api() -> Any:
    try:
        maybe_run_retention_cleanup()
        ensure_ffmpeg_tools()

        payload = request.get_json(force=True)
        input_path_raw = (payload.get('input_path') or '').strip()
        output_dir_raw = (payload.get('output_dir') or str(DEFAULT_OUTPUT_DIR)).strip()
        slate_background_image_raw = (payload.get('slate_background_image') or '').strip()

        if not input_path_raw:
            return jsonify({'error': 'Input path is required.'}), 400

        input_path = Path(input_path_raw).expanduser().resolve()
        if not input_path.exists():
            return jsonify({'error': f'Input file not found: {input_path}'}), 400
        source_stem = input_path.stem.strip() or 'delivery'

        output_dir = Path(output_dir_raw).expanduser().resolve()
        slate_background_image = None
        if slate_background_image_raw:
            slate_background_image = Path(slate_background_image_raw).expanduser().resolve()
            if not slate_background_image.exists():
                return jsonify({'error': f'Slate background image not found: {slate_background_image}'}), 400

        profiles = load_profiles()
        selected = payload.get('profiles') or []
        selected = [p for p in selected if p]
        if not selected:
            return jsonify({'error': 'Select at least one deliverable profile.'}), 400

        slate = payload.get('slate') or {}
        slate_layout = payload.get('slate_layout') or {}
        spot_duration_mode = str(payload.get('spot_duration_mode') or 'auto')
        source_duration = probe_media(input_path)['duration']
        requested_duration = source_duration
        if spot_duration_mode != 'auto':
            requested_duration = normalize_positive_number(spot_duration_mode, source_duration)

        selected_profiles = selected[:2]
        extension_counts: Dict[str, int] = {}
        profile_extensions: Dict[str, str] = {}
        for profile_key in selected_profiles:
            profile = profiles.get(profile_key)
            if not profile:
                return jsonify({'error': f'Unknown profile: {profile_key}'}), 400
            ext = normalize_output_extension(
                profile.get('output_extension', ''),
                str(profile.get('video_codec', 'prores_ks')),
                infer_output_extension(str(profile.get('video_codec', 'prores_ks'))),
            )
            profile_extensions[profile_key] = ext
            extension_counts[ext] = extension_counts.get(ext, 0) + 1

        results: List[Dict[str, Any]] = []
        for profile_key in selected_profiles:
            if profile_key not in profiles:
                return jsonify({'error': f'Unknown profile: {profile_key}'}), 400
            output_filename_stem = source_stem
            if extension_counts.get(profile_extensions.get(profile_key, ''), 0) > 1:
                output_filename_stem = f'{source_stem}_{profile_key}'
            result = run_profile_render(
                input_path=input_path,
                output_dir=output_dir,
                output_filename_stem=output_filename_stem,
                profile_key=profile_key,
                profile=profiles[profile_key],
                slate=slate,
                slate_layout=slate_layout,
                slate_background_image=slate_background_image,
                requested_spot_duration=requested_duration,
            )
            output_path = Path(result.get('output_path', '')).expanduser().resolve()
            if output_path.exists() and output_path.is_file():
                token = register_download_token(output_path)
                result['download_url'] = f'/api/download/{token}'
            results.append(result)

        return jsonify({'ok': True, 'results': results})
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


if __name__ == '__main__':
    debug_mode = os.getenv('SPOT_DELIVERY_DEBUG', '0') == '1'
    app.run(host='0.0.0.0', port=3040, debug=debug_mode, use_reloader=False)
