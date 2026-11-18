import os
import re
import io
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# ==========================================
# ZeroTV (ZT) - "Melayani dengan sepenuh hati"
# Satu Playlist untuk Segala Tontonan | Streaming Tanpa Batas
# ==========================================

FOLDER_KUMPULAN_SUMBER_ID = "1F6vI-kLMCS9OSXHmaNeILVIOo8GSoIup"
FOLDER_SIARAN_NASIONAL_ID = "135gCD1xTe7heWHNc4_oQk3HYrLC4vZA-"

SOURCES = [
    {
        "owner": "apistech",
        "repo": "apistech/project",
        "url": "https://raw.githubusercontent.com/apistech/project/refs/heads/main/IndihomeTV.m3u",
    },
    {
        "owner": "azamstv00-cpu",
        "repo": "azamstv00-cpu/Public_Iptv_Channels",
        "url": "https://raw.githubusercontent.com/azamstv00-cpu/Public_Iptv_Channels/main/playlist.m3u8",
    },
    {
        "owner": "time2shine",
        "repo": "time2shine/Rokon-IPTV",
        "url": "https://raw.githubusercontent.com/time2shine/Rokon-IPTV/main/playlist.m3u",
    },
    {
        "owner": "alantang1977",
        "repo": "alantang1977/yuanzl77",
        "url": "https://raw.githubusercontent.com/alantang1977/yuanzl77/main/live.m3u",
    },
    {
        "owner": "dearbulut",
        "repo": "dearbulut/iptv",
        "url": "https://dearbulut.github.io/iptv/playlists/online.m3u",
        "skip_recheck": True,
    },
    {
        "owner": "BuddyChewChew",
        "repo": "BuddyChewChew/app-m3u-generator",
        "url": "https://raw.githubusercontent.com/BuddyChewChew/app-m3u-generator/main/playlists/plutotv_all.m3u",
    },
    {
        "owner": "BuddyChewChew",
        "repo": "BuddyChewChew/app-m3u-generator",
        "url": "https://raw.githubusercontent.com/BuddyChewChew/app-m3u-generator/main/playlists/samsungtvplus_all.m3u",
    },
    {
        "owner": "drmlive",
        "repo": "drmlive/fancode-live-events",
        "url": "https://raw.githubusercontent.com/drmlive/fancode-live-events/main/fancode.m3u",
    },
    {
        "owner": "byte-capsule",
        "repo": "byte-capsule/FanCode-Hls-Fetcher",
        "url": "https://raw.githubusercontent.com/byte-capsule/FanCode-Hls-Fetcher/main/Fancode_Live.m3u",
    },
    {
        "owner": "dmandache",
        "repo": "dmandache/my-iptv-playlist",
        "url": "https://raw.githubusercontent.com/dmandache/my-iptv-playlist/master/tv.m3u",
    },
    {
        "owner": "Romaxa55",
        "repo": "Romaxa55/world_ip_tv",
        "url": "https://romaxa55.github.io/world_ip_tv/output/index.m3u",
    },
]

# Nama TV Nasional & Daerah Indonesia yang spesifik (tanpa singkatan ambigu luar negeri)
NASIONAL_EXACT_PATTERN = re.compile(
    r"\b("
    r"rcti|sctv|indosiar|trans\s*tv|trans\s*7|trans7|antv|tvri(?:\s+\w+)?|"
    r"gtv|inews|metro\s*tv|tvone|tv\s*one|kompas\s*tv|cnbc\s*indonesia|"
    r"cnn\s*indonesia|mdtv|moji|mnctv|nusantara\s*tv|garuda\s*tv|berita\s*satu|"
    r"mentari\s*tv|sinpo\s*tv|jawa\s*pos\s*tv|banjar\s*tv|duta\s*tv|jtv"
    r")\b",
    re.IGNORECASE,
)

# Platform & penanda sumber Indonesia (CubMu, DENS TV, MaxStream, Vidio, Vision+, Digital, RCTI+, IndiHomeTV)
INDO_PLATFORM_PATTERN = re.compile(
    r"(cubmu|dens\.?tv|maxstream|vidio|vision\s*plus|vision\+|rcti\s*\+|rctiplus|"
    r"indihome|digital\s*tv|indonesia|nasional|\.id\b|detik\.com|tvri\.go\.id)",
    re.IGNORECASE,
)

# Singkatan pendek yang hanya valid jika disertai konteks Indonesia (tvg-id .id / kategori / URL Indonesia)
AMBIGUOUS_INDO_PATTERN = re.compile(
    r"\b(btv|rtv|vtv|net\.?\s*tv|net\.?)\b",
    re.IGNORECASE,
)

HEADERS = {"User-Agent": "TiviMate/4.7.0 (Android)"}
TIMEOUT_FETCH = 15
TIMEOUT_CHECK = 3
MAX_WORKERS = 100


def is_siaran_nasional(entry):
    combined_meta = f"{entry['extinf']} {entry['category']} {entry['title']} {entry['url']}"
    has_indo_context = bool(INDO_PLATFORM_PATTERN.search(combined_meta))

    if NASIONAL_EXACT_PATTERN.search(entry["title"]):
        return True
    if has_indo_context:
        if AMBIGUOUS_INDO_PATTERN.search(entry["title"]) or any(
            k in entry["category"].lower()
            for k in ("indonesia", "nasional", "cubmu", "dens", "maxstream", "vidio", "vision", "rcti", "indihome", "digital")
        ):
            return True
        if INDO_PLATFORM_PATTERN.search(f"{entry['title']} {entry['url']}"):
            return True
    return False


def get_drive_service():
    creds = Credentials(
        token=None,
        refresh_token=os.environ["REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["CLIENT_ID"],
        client_secret=os.environ["CLIENT_SECRET"],
        scopes=["https://www.googleapis.com/auth/drive"],
    )
    creds.refresh(Request())
    return build("drive", "v3", credentials=creds)


def list_children(service, parent_id):
    items = []
    page_token = None
    while True:
        res = (
            service.files()
            .list(
                q=f"'{parent_id}' in parents and trashed = false",
                fields="nextPageToken, files(id, name, mimeType)",
                pageSize=200,
                pageToken=page_token,
            )
            .execute()
        )
        items.extend(res.get("files", []))
        page_token = res.get("nextPageToken")
        if not page_token:
            break
    return items


def cleanup_stale_items(service, parent_id, keep_names):
    for item in list_children(service, parent_id):
        if item["name"] not in keep_names:
            try:
                service.files().delete(fileId=item["id"]).execute()
                print(f"[AUTO-DELETE] {item['name']}", flush=True)
            except Exception as exc:
                print(f"[DELETE-SKIP] {item['name']} -> {exc}", flush=True)


def get_or_create_folder(service, name, parent_id):
    safe_name = name.replace("'", "\\'")
    q = (
        f"name = '{safe_name}' and '{parent_id}' in parents "
        f"and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    )
    res = service.files().list(q=q, fields="files(id, name)").execute()
    files = res.get("files", [])
    if files:
        return files[0]["id"]
    meta = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    folder = service.files().create(body=meta, fields="id").execute()
    return folder["id"]


def upload_or_update_text(service, filename, content, parent_id, mime_type="text/plain"):
    safe_name = filename.replace("'", "\\'")
    q = f"name = '{safe_name}' and '{parent_id}' in parents and trashed = false"
    res = service.files().list(q=q, fields="files(id)").execute()
    files = res.get("files", [])
    media = MediaIoBaseUpload(
        io.BytesIO(content.encode("utf-8")), mimetype=mime_type, resumable=True
    )
    if files:
        file_id = files[0]["id"]
        service.files().update(fileId=file_id, media_body=media).execute()
        for dup in files[1:]:
            try:
                service.files().delete(fileId=dup["id"]).execute()
            except Exception:
                pass
        return file_id
    meta = {"name": filename, "parents": [parent_id]}
    created = service.files().create(body=meta, media_body=media, fields="id").execute()
    return created["id"]


def sanitize_filename(name):
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", name).strip()
    return cleaned[:80] if cleaned else "Uncategorized"


def parse_m3u(text):
    entries = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#EXTINF"):
            extinf = line
            extra_lines = []
            j = i + 1
            stream_url = None
            while j < len(lines):
                nxt = lines[j].strip()
                if not nxt:
                    j += 1
                    continue
                if nxt.startswith("#EXTINF"):
                    break
                if nxt.startswith("#"):
                    extra_lines.append(nxt)
                else:
                    stream_url = nxt
                    j += 1
                    break
                j += 1

            if stream_url and stream_url.startswith(("http://", "https://")):
                grp_m = re.search(r'group-title="([^"]*)"', extinf, re.IGNORECASE)
                category = grp_m.group(1).strip() if grp_m and grp_m.group(1).strip() else "Umum"
                title = extinf.split(",", 1)[-1].strip() if "," in extinf else "Unknown"
                entries.append(
                    {
                        "extinf": extinf,
                        "extra": extra_lines,
                        "url": stream_url,
                        "category": category,
                        "title": title,
                    }
                )
            i = j
        else:
            i += 1
    return entries


def check_stream(url):
    try:
        r = requests.head(url, headers=HEADERS, timeout=TIMEOUT_CHECK, allow_redirects=True)
        if r.status_code in (200, 206, 301, 302):
            return True
        if r.status_code in (403, 405, 501):
            r = requests.get(
                url, headers=HEADERS, timeout=TIMEOUT_CHECK, stream=True, allow_redirects=True
            )
            ok = r.status_code in (200, 206)
            r.close()
            return ok
        return False
    except requests.RequestException:
        return False


def build_m3u_content(entries):
    header = (
        '#EXTM3U x-tvg-url=""\n'
        '# ZeroTV (ZT) - Satu Playlist untuk Segala Tontonan\n'
        '# Streaming Tanpa Batas | Melayani dengan sepenuh hati\n'
    )
    blocks = []
    for e in entries:
        lines = [e["extinf"]] + e["extra"] + [e["url"]]
        blocks.append("\n".join(lines))
    return header + "\n".join(blocks) + "\n"


def main():
    print("Memulai sinkronisasi ZeroTV Sumber PL...", flush=True)
    service = get_drive_service()
    all_nasional_entries = []
    any_fetch_ok = False
    owner_stats = defaultdict(
        lambda: {
            "repos": set(),
            "urls": [],
            "fetch_ok": False,
            "total": 0,
            "active": 0,
            "categories": defaultdict(list),
            "nasional": [],
        }
    )

    for src in SOURCES:
        owner = src["owner"]
        owner_stats[owner]["repos"].add(src["repo"])
        owner_stats[owner]["urls"].append(src["url"])

        try:
            print(f"[FETCH] {src['repo']} -> {src['url']}", flush=True)
            resp = requests.get(src["url"], headers=HEADERS, timeout=TIMEOUT_FETCH)
            resp.raise_for_status()
            entries = parse_m3u(resp.text)
            owner_stats[owner]["fetch_ok"] = True
            any_fetch_ok = True
        except Exception as exc:
            print(f"[SKIP] {src['url']} -> {exc}", flush=True)
            continue

        owner_stats[owner]["total"] += len(entries)

        if src.get("skip_recheck"):
            print(f"  -> {len(entries)} siaran (lolos health-check asal)", flush=True)
            active_map = {e["url"]: True for e in entries}
        else:
            unique_urls = list({e["url"] for e in entries})
            print(f"  -> Mengecek {len(unique_urls)} stream unik...", flush=True)
            active_map = {}
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
                future_to_url = {pool.submit(check_stream, u): u for u in unique_urls}
                for fut in as_completed(future_to_url):
                    u = future_to_url[fut]
                    active_map[u] = fut.result()

        for e in entries:
            if active_map.get(e["url"], False):
                owner_stats[owner]["active"] += 1
                owner_stats[owner]["categories"][e["category"]].append(e)
                if is_siaran_nasional(e):
                    owner_stats[owner]["nasional"].append(e)
                    all_nasional_entries.append(e)

    expected_nasional_items = set()

    for owner, data in owner_stats.items():
        if not data["fetch_ok"]:
            continue

        print(f"[UPLOAD] Owner: {owner} ({data['active']}/{data['total']} aktif, {len(data['nasional'])} nasional)", flush=True)
        owner_folder_id = get_or_create_folder(service, owner, FOLDER_KUMPULAN_SUMBER_ID)
        expected_owner_files = {"RINGKASAN_OWNER.txt"}

        summary_lines = [
            "ZeroTV (ZT) - Ringkasan Sumber Playlist",
            "Satu Playlist untuk Segala Tontonan | Streaming Tanpa Batas",
            "Melayani dengan sepenuh hati",
            "",
            f"Nama Owner       : {owner}",
            f"Nama Repo        : {', '.join(sorted(data['repos']))}",
            f"Link URL         : {', '.join(data['urls'])}",
            f"Jumlah Total     : {data['total']}",
            f"Jumlah Aktif     : {data['active']}",
            f"Siaran Nasional  : {len(data['nasional'])}",
            "",
            "Jumlah Siaran Aktif Per Kategori:",
        ]

        for cat, cat_entries in sorted(data["categories"].items()):
            summary_lines.append(f"- {cat}: {len(cat_entries)} siaran aktif")
            cat_filename = f"{sanitize_filename(cat)}.m3u"
            expected_owner_files.add(cat_filename)
            upload_or_update_text(
                service, cat_filename, build_m3u_content(cat_entries), owner_folder_id, "audio/x-mpegurl"
            )

        upload_or_update_text(service, "RINGKASAN_OWNER.txt", "\n".join(summary_lines), owner_folder_id)
        cleanup_stale_items(service, owner_folder_id, expected_owner_files)

        if data["nasional"]:
            expected_nasional_items.add(owner)
            nas_owner_folder_id = get_or_create_folder(service, owner, FOLDER_SIARAN_NASIONAL_ID)
            nas_filename = f"Siaran_Nasional_{sanitize_filename(owner)}.m3u"
            upload_or_update_text(
                service,
                nas_filename,
                build_m3u_content(data["nasional"]),
                nas_owner_folder_id,
                "audio/x-mpegurl",
            )
            cleanup_stale_items(service, nas_owner_folder_id, {nas_filename})

    if all_nasional_entries:
        print(f"[UPLOAD] Semua_Siaran_Nasional_Aktif.m3u ({len(all_nasional_entries)} siaran)", flush=True)
        expected_nasional_items.add("Semua_Siaran_Nasional_Aktif.m3u")
        upload_or_update_text(
            service,
            "Semua_Siaran_Nasional_Aktif.m3u",
            build_m3u_content(all_nasional_entries),
            FOLDER_SIARAN_NASIONAL_ID,
            "audio/x-mpegurl",
        )

    if any_fetch_ok:
        expected_sumber_folders = {s["owner"] for s in SOURCES}
        cleanup_stale_items(service, FOLDER_KUMPULAN_SUMBER_ID, expected_sumber_folders)
        cleanup_stale_items(service, FOLDER_SIARAN_NASIONAL_ID, expected_nasional_items)

    print("Sinkronisasi Sumber PL ke Google Drive selesai.", flush=True)


if __name__ == "__main__":
    main()
