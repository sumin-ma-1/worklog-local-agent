from __future__ import annotations

from pathlib import Path

# Must stay in sync with web/static/file-icons/*.svg
_FILE_ICON_KEYS = frozenset(
    {
        "7z",
        "aac",
        "ai",
        "avi",
        "bmp",
        "bz2",
        "c",
        "conf",
        "cpp",
        "cs",
        "css",
        "csv",
        "db",
        "deb",
        "dmg",
        "doc",
        "dockerfile",
        "docm",
        "docx",
        "dropbox",
        "eml",
        "eps",
        "etc",
        "exe",
        "flac",
        "flv",
        "gdrive",
        "gif",
        "go",
        "gz",
        "htm",
        "html",
        "hwp",
        "ics",
        "img",
        "indd",
        "ini",
        "ipynb",
        "iso",
        "java",
        "jpeg",
        "jpg",
        "js",
        "json",
        "key",
        "log",
        "m4a",
        "mat",
        "mbox",
        "md",
        "mkv",
        "mov",
        "mp3",
        "mp4",
        "msg",
        "msi",
        "numbers",
        "ogg",
        "one",
        "onex",
        "pages",
        "pdf",
        "php",
        "pkg",
        "png",
        "ppt",
        "pptm",
        "pptx",
        "psd",
        "pst",
        "py",
        "r",
        "rar",
        "rb",
        "rdata",
        "rpm",
        "sql",
        "sqlite",
        "svg",
        "tar",
        "tiff",
        "tsv",
        "txt",
        "url",
        "vcf",
        "wav",
        "webm",
        "webp",
        "wmv",
        "xls",
        "xlsm",
        "xlsx",
        "xml",
        "yaml",
        "yml",
        "zip",
    }
)

_ALIASES = {
    "tif": "tiff",
    "htm": "html",
    "jpeg": "jpg",
    "markdown": "md",
    "yml": "yaml",
}


def file_icon_key(filename: str | None) -> str:
    name = Path(str(filename or "")).name.lower()
    if not name or "." not in name:
        return "etc"
    ext = name.rsplit(".", 1)[-1]
    key = _ALIASES.get(ext, ext)
    if key in _FILE_ICON_KEYS:
        return key
    return "etc"


def file_icon_src(filename: str | None) -> str:
    return f"/static/file-icons/{file_icon_key(filename)}.svg"
