"""Собирает kurs.md -> kurs.html -> «Ключник — курс по Telegram-ботам.pdf» через headless Edge/Chrome."""
import html
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "kurs.md"
HTML_OUT = HERE / "kurs.html"
PDF_OUT = HERE / "Ключник — курс по Telegram-ботам.pdf"

BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
]

CSS = """
@page { size: A4; margin: 18mm 16mm 18mm 16mm; }
body { font-family: 'Segoe UI', Arial, sans-serif; font-size: 11pt; line-height: 1.5; color: #1d1d1f; }
h1 { font-size: 34pt; margin: 120px 0 4px; letter-spacing: -0.5px; }
h1 + h3 { font-size: 15pt; font-weight: 400; color: #555; margin-top: 0; }
h2 { font-size: 19pt; page-break-before: always; border-bottom: 2px solid #1d1d1f; padding-bottom: 6px; margin-top: 0; }
h3 { font-size: 13pt; margin: 20px 0 6px; }
p { margin: 6px 0 8px; }
ul, ol { margin: 6px 0 10px; padding-left: 22px; }
li { margin: 3px 0; }
code { font-family: Consolas, 'Courier New', monospace; font-size: 9.5pt; background: #f1f1f3; padding: 1px 4px; border-radius: 3px; }
pre { font-family: Consolas, 'Courier New', monospace; font-size: 9pt; line-height: 1.4; background: #f6f6f8;
      border: 1px solid #e2e2e6; border-radius: 6px; padding: 10px 12px; white-space: pre-wrap; word-break: break-word; }
pre code { background: none; padding: 0; font-size: inherit; }
.box { padding: 10px 14px; margin: 14px 0; border-radius: 0 6px 6px 0; page-break-inside: avoid; border-left: 4px solid; }
.key { border-color: #b07d12; background: #fbf6ea; }
.vibe { border-color: #7b4bd1; background: #f4effc; }
.dev { border-color: #1f7a5a; background: #ecf7f2; }
"""


def inline(text: str) -> str:
    codes = []

    def keep(m):
        codes.append("<code>" + html.escape(m.group(1)) + "</code>")
        return f"\x00{len(codes) - 1}\x00"

    text = re.sub(r"`([^`]+)`", keep, text)
    text = html.escape(text, quote=False)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![\w*])\*([^*\n]+?)\*(?![\w*])", r"<em>\1</em>", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], text)


def convert(md: str) -> str:
    out, para, lst, quote = [], [], None, []
    lines = md.splitlines()

    def flush():
        nonlocal para, lst, quote
        if para:
            out.append("<p>" + inline(" ".join(para)) + "</p>")
            para = []
        if lst:
            tag, items = lst
            out.append(f"<{tag}>" + "".join(f"<li>{inline(i)}</li>" for i in items) + f"</{tag}>")
            lst = None
        if quote:
            text = " ".join(quote)
            kind = "vibe" if text.startswith("**Вайб") else "dev" if text.startswith("**Кодеру") else "key"
            out.append(f'<div class="box {kind}">' + inline(text) + "</div>")
            quote = []

    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            flush()
            block = []
            i += 1
            while not lines[i].startswith("```"):
                block.append(lines[i])
                i += 1
            out.append("<pre><code>" + html.escape("\n".join(block)) + "</code></pre>")
        elif m := re.match(r"(#{1,3}) (.*)", line):
            flush()
            n = len(m.group(1))
            out.append(f"<h{n}>{inline(m.group(2))}</h{n}>")
        elif line.startswith("> "):
            if para or lst:
                flush()
            quote.append(line[2:])
        elif m := re.match(r"- (.*)", line):
            if para or quote or (lst and lst[0] != "ul"):
                flush()
            lst = lst or ("ul", [])
            lst[1].append(m.group(1))
        elif m := re.match(r"\d+\. (.*)", line):
            if para or quote or (lst and lst[0] != "ol"):
                flush()
            lst = lst or ("ol", [])
            lst[1].append(m.group(1))
        elif not line.strip():
            flush()
        else:
            if lst or quote:
                flush()
            para.append(line.strip())
        i += 1
    flush()
    return "\n".join(out)


def main():
    body = convert(SRC.read_text(encoding="utf-8"))
    HTML_OUT.write_text(
        f'<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Ключник</title>'
        f"<style>{CSS}</style></head><body>{body}</body></html>",
        encoding="utf-8",
    )
    browser = next((b for b in BROWSERS if Path(b).exists()), None)
    if browser is None:
        sys.exit("Не нашёл Edge или Chrome. Открой kurs.html в браузере и сохрани как PDF.")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_pdf = Path(tmp) / "kurs.pdf"
        subprocess.run(
            [browser, "--headless", "--disable-gpu", "--no-first-run", "--disable-extensions",
             "--no-pdf-header-footer", f"--user-data-dir={tmp}\\profile",
             f"--print-to-pdf={tmp_pdf}", HTML_OUT.as_uri()],
            check=True, timeout=120, stdin=subprocess.DEVNULL,
        )
        shutil.copy(tmp_pdf, PDF_OUT)
    print("Готово:", PDF_OUT)


if __name__ == "__main__":
    main()
