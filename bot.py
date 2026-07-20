import os, sys, re, time, json, random, threading, subprocess, urllib.parse, io, math
from datetime import datetime, timedelta
from typing import Optional, Tuple, Any

import requests
from playwright.sync_api import sync_playwright, TimeoutError
from PIL import Image, ImageDraw, ImageFont

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# Try import captcha solver
try:
    from captcha_solver import solve_rotate_captcha_robust, solve_slide_puzzle
    CAPTCHA_SOLVER_AVAILABLE = True
except Exception:
    CAPTCHA_SOLVER_AVAILABLE = False

# === CONFIG ===
DOWNLOADS_DIR = "downloads"
os.makedirs(DOWNLOADS_DIR, exist_ok=True)
PROFILE_BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "playwright-profile")
os.makedirs(PROFILE_BASE, exist_ok=True)

TIKWM_API = "https://www.tikwm.com/api/"
TIKWM_SEARCH_API = "https://www.tikwm.com/api/feed/search"

POST_INTERVAL_SECONDS = 300
VIDEO_CHOICE_POOL = 6

# === GLOBALS ===
workers = {}
browser_sessions = {}
screenshots = {}
last_frame_ts = {}

# === LOGGING ===
def _persist(username, message):
    try:
        from database import append_log
        append_log(username, message)
    except Exception:
        pass

def log(msg, username=""):
    print(msg, flush=True)
    # Auto-detect a "[user] ..." prefix so callers don't have to pass username.
    if not username:
        m = re.match(r"^\[([^\]]+)\]\s", str(msg))
        if m:
            username = m.group(1)
    if username:
        _persist(username, str(msg))

def _log_event(username, message):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{username}] {message}")
    _persist(username, message)

# === SCREENSHOT / LIVE CAM ===
def create_placeholder(username, text):
    img = Image.new("RGB", (800, 450), "#111111")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
    except Exception:
        font = ImageFont.load_default()
    draw.text((30, 30), f"TOR BOT - {username}", fill="#ff0050", font=font)
    draw.text((30, 80), text, fill="white", font=font)
    draw.text((30, 400), datetime.now().strftime("%H:%M:%S"), fill="#888", font=font)
    return img

def take_screenshot(username):
    session = browser_sessions.get(username)
    if not session:
        screenshots[username] = create_placeholder(username, "No browser")
        return
    owner = session.get("owner_thread")
    if owner is not None and owner is not threading.current_thread():
        return
    try:
        page = session.get("page")
        if page is None or page.is_closed():
            try:
                ctx_pages = session["context"].pages
                if ctx_pages:
                    page = ctx_pages[-1]
                    session["page"] = page
                else:
                    if username not in screenshots:
                        screenshots[username] = create_placeholder(username, "Page closed")
                    return
            except Exception:
                if username not in screenshots:
                    screenshots[username] = create_placeholder(username, "Browser closed")
                return
        screenshot_bytes = page.screenshot(type="png", timeout=15000)
        img = Image.open(io.BytesIO(screenshot_bytes)).convert("RGB")
        screenshots[username] = img
        last_frame_ts[username] = time.time()
    except Exception as e:
        err = str(e).split("\n")[0][:60]
        if username not in screenshots:
            screenshots[username] = create_placeholder(username, f"Screenshot error: {err}")

# === TOR MANAGEMENT ===
def _start_tor_instance(username, port_offset=0):
    """Start a dedicated Tor process for this account."""
    tor_data_dir = os.path.join(PROFILE_BASE, f"tor_data_{username}")
    os.makedirs(tor_data_dir, exist_ok=True)
    socks_port = 9050 + port_offset
    control_port = 9051 + port_offset
    
    torrc_path = os.path.join(tor_data_dir, "torrc")
    with open(torrc_path, "w") as f:
        f.write(f"SocksPort {socks_port}\n")
        f.write(f"ControlPort {control_port}\n")
        f.write(f"DataDirectory {tor_data_dir}\n")
        f.write("CookieAuthentication 0\n")
        f.write("MaxCircuitDirtiness 10\n")
    
    tor_path = os.environ.get("TOR_PATH", "tor")
    try:
        proc = subprocess.Popen(
            [tor_path, "-f", torrc_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    except FileNotFoundError:
        log(f"[{username}] Tor binary '{tor_path}' not found — continuing without Tor proxy.")
        return None, None, None
    time.sleep(3)
    return proc, socks_port, control_port

def _new_tor_identity(control_port):
    """Request a new Tor circuit."""
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect(("127.0.0.1", control_port))
        s.send(b"AUTHENTICATE \"\"\r\n")
        s.recv(1024)
        s.send(b"SIGNAL NEWNYM\r\n")
        s.recv(1024)
        s.send(b"QUIT\r\n")
        s.close()
        time.sleep(2)
        return True
    except Exception:
        return False

# === BROWSER SESSION ===
def _profile_dir_for(username):
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", username or "default")
    return os.path.join(PROFILE_BASE, safe)

def _clear_stale_profile_lock(profile_dir):
    try:
        import glob
        for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            lock = os.path.join(profile_dir, name)
            if os.path.exists(lock):
                os.remove(lock)
        for lock in glob.glob(os.path.join(profile_dir, "*", "SingletonLock")):
            try: os.remove(lock)
            except Exception: pass
    except Exception:
        pass

def _start_browser_session(username, tor_socks_port=None):
    profile_dir = _profile_dir_for(username)
    os.makedirs(profile_dir, exist_ok=True)
    
    if username in browser_sessions:
        try:
            browser_sessions[username]["context"].close()
            browser_sessions[username]["browser"].close()
            browser_sessions[username]["pw"].stop()
        except Exception:
            pass
        del browser_sessions[username]
    
    proxy = None
    if tor_socks_port:
        proxy = {"server": f"socks5://127.0.0.1:{tor_socks_port}"}
    
    pw = sync_playwright().start()
    _clear_stale_profile_lock(profile_dir)
    
    base_kwargs = dict(
        user_data_dir=profile_dir,
        viewport={"width": 1280, "height": 720},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.0.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        locale="en-US",
        ignore_https_errors=True,
        args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--no-first-run",
            "--no-default-browser-check",
            "--window-size=1280,720",
            "--disable-webrtc",
            "--force-webrtc-ip-handling-policy=default_public_interface_only",
        ],
    )
    
    if proxy:
        base_kwargs["proxy"] = proxy
    
    context = None
    for attempt_headless in (False, True):
        launch_kwargs = dict(base_kwargs, headless=attempt_headless)
        try:
            context = pw.chromium.launch_persistent_context(**launch_kwargs)
            break
        except Exception as le:
            print(f"[{username}] launch (headless={attempt_headless}) failed: {le}")
            continue
    
    if context is None:
        raise RuntimeError("Could not launch browser")
    
    page = context.pages[0] if context.pages else context.new_page()
    try:
        context.add_init_script("""() => {
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            window.chrome = { runtime: {} };
        }""")
    except Exception:
        pass
    
    session = {
        "pw": pw, "browser": context, "context": context, "page": page,
        "profile_dir": profile_dir, "owner_thread": threading.current_thread()
    }
    browser_sessions[username] = session
    return session

# === CAPTCHA DETECTION & SOLVING ===
def _detect_tiktok_captcha(page) -> Optional[str]:
    if page is None:
        return None
    try:
        keywords = ["drag the slider", "fit the puzzle", "puzzle", "slider",
                    "drag to", "slide to", "rotate", "whirl", "turn the",
                    "align", "verify your", "security check", "captcha",
                    "choose correct image", "select all", "click verify"]
        containers = ['div[role="dialog"]', '[class*="captcha"]', '[class*="verify"]',
                      '[class*="slide"]', '[class*="puzzle"]', 'div[aria-modal="true"]',
                      '.geetest', '[data-e2e*="captcha"]', '[data-e2e*="verify"]']
        for sel in containers:
            try:
                container = page.locator(sel).first
                if container.count() > 0 and container.is_visible():
                    text = (container.inner_text(timeout=1500) or "").lower()
                    if any(kw in text for kw in keywords):
                        if any(k in text for k in ["rotate", "whirl", "turn"]):
                            return "rotate"
                        if any(k in text for k in ["choose", "select all", "click verify"]):
                            return "image_select"
                        if any(k in text for k in ["drag", "slider", "puzzle", "fit"]):
                            return "slide"
                        return "slide"
            except:
                pass
        
        try:
            body_text = (page.inner_text("body", timeout=2000) or "").lower()
            if any(kw in body_text for kw in keywords):
                if any(k in body_text for k in ["rotate", "whirl", "turn"]):
                    return "rotate"
                if any(k in body_text for k in ["choose", "select all", "click verify"]):
                    return "image_select"
                if any(k in body_text for k in ["drag", "slider", "puzzle", "fit the"]):
                    return "slide"
                return "slide"
        except:
            pass
        
        slider_selectors = ['input[type="range"]', '[class*="slider"]', '[class*="geetest"]',
                            'canvas', '[role="slider"]', 'button[aria-label*="slide"]']
        for sel in slider_selectors:
            try:
                el = page.locator(sel).first
                if el.count() > 0 and el.is_visible():
                    return "slide"
            except:
                pass
        return None
    except Exception as e:
        print(f"[captcha] Detection error: {str(e)[:60]}")
        return None

def _extract_captcha_images(page):
    try:
        captcha_box = page.locator('div[role="dialog"], .verify-container, [class*="captcha"]').first
        if captcha_box.count() == 0:
            captcha_box = page.locator('body')
        full_bytes = captcha_box.screenshot(timeout=8000)
        
        canvases = page.locator('canvas')
        imgs = page.locator('img')
        elements = []
        for i in range(min(canvases.count(), 4)):
            try: elements.append(canvases.nth(i))
            except: pass
        for i in range(min(imgs.count(), 4)):
            try: elements.append(imgs.nth(i))
            except: pass
        
        outer = inner = None
        if len(elements) >= 2:
            try:
                outer = elements[0].screenshot(timeout=6000)
                inner = elements[-1].screenshot(timeout=6000)
            except: pass
        
        if not outer: outer = full_bytes
        if not inner: inner = full_bytes
        
        return outer, inner
    except Exception as e:
        print(f"[captcha] Image extraction error: {str(e)[:80]}")
        return None, None

def solve_tiktok_captcha(page, username=""):
    if not CAPTCHA_SOLVER_AVAILABLE:
        return False
    
    captcha_type = _detect_tiktok_captcha(page)
    if not captcha_type:
        return True
    
    print(f"[{username}] CAPTCHA DETECTED: {captcha_type}")
    
    if captcha_type == "rotate":
        outer, inner = _extract_captcha_images(page)
        if not outer or not inner:
            return False
        angle, conf = solve_rotate_captcha_robust(outer, inner, debug=True)
        print(f"[{username}] Solved angle: {angle}° (confidence: {conf}%)")
        
        try:
            slider = page.locator('[data-e2e*="slider"], .slider, input[type=range], div[role="slider"]').first
            if slider.count() == 0:
                slider = page.locator('div[style*="cursor"], circle, [class*="handle"]').first
            
            if slider.count() > 0:
                box = slider.bounding_box(timeout=5000)
                if box:
                    slider_width = box['width'] or 280
                    drag_distance = (angle / 360.0) * slider_width * 1.05
                    start_x = box['x'] + 15
                    start_y = box['y'] + box['height'] / 2
                    
                    page.mouse.move(start_x, start_y)
                    page.mouse.down()
                    time.sleep(0.12)
                    
                    steps = max(8, int(abs(drag_distance) / 18))
                    for i in range(steps):
                        progress = (i + 1) / steps
                        curr_x = start_x + (drag_distance * progress)
                        page.mouse.move(curr_x, start_y, steps=1)
                        time.sleep(0.018)
                    
                    page.mouse.up()
                    time.sleep(1.2)
                    print(f"[{username}] Dragged slider by ~{drag_distance:.0f}px")
            else:
                circle = page.locator('canvas, .captcha-circle, [class*="rotate-container"]').first
                if circle.count() > 0:
                    cbox = circle.bounding_box()
                    if cbox:
                        cx = cbox['x'] + cbox['width']/2
                        cy = cbox['y'] + cbox['height']/2
                        page.mouse.move(cx, cy)
                        page.mouse.down()
                        for i in range(12):
                            rad = math.radians(angle * (i/12))
                            nx = cx + math.cos(rad) * 80
                            ny = cy + math.sin(rad) * 80
                            page.mouse.move(nx, ny)
                            time.sleep(0.04)
                        page.mouse.up()
            
            time.sleep(2)
            take_screenshot(username)
            
            for btn_text in ["Verify", "Submit", "Confirm", "Done"]:
                try:
                    btn = page.locator(f'button:has-text("{btn_text}")').first
                    if btn.count() > 0 and btn.is_visible():
                        btn.click(timeout=4000)
                        time.sleep(1.5)
                        break
                except: pass
            
            time.sleep(2.5)
            return True
        except Exception as e:
            print(f"[{username}] Drag error: {str(e)[:70]}")
            return False
    
    elif captcha_type == "image_select":
        # "Select all images that contain X" style captcha.
        # Best-effort: wait for candidate tiles, click the first one, then
        # submit. (A true solver needs an image-classification model; this
        # at least drives the UI so the flow can progress / retry.)
        try:
            tiles = page.locator('img[class*="image"], [class*="captcha"] img, div[role="img"], [class*="verify"] img')
            count = tiles.count()
            if count == 0:
                print(f"[{username}] Image select: no tiles found")
                return False
            # Click the first candidate tile (placeholder selection).
            tiles.nth(0).click(timeout=3000, force=True)
            time.sleep(0.6)
            for btn_text in ["Verify", "Submit", "Confirm", "Done", "OK"]:
                try:
                    btn = page.locator(f'button:has-text("{btn_text}")').first
                    if btn.count() > 0 and btn.is_visible():
                        btn.click(timeout=4000)
                        time.sleep(1.5)
                        break
                except Exception:
                    pass
            print(f"[{username}] Image select: clicked 1/{count} tiles and submitted")
            return True
        except Exception as e:
            print(f"[{username}] Image select error: {e}")
            return False

    elif captcha_type == "slide":
        # Standard slide (puzzle) captcha - solve via OpenCV template matching.
        outer, inner = _extract_captcha_images(page)
        if not (outer and inner and CAPTCHA_SOLVER_AVAILABLE):
            print(f"[{username}] Slide: missing images or solver unavailable")
            return False
        try:
            offset = solve_slide_puzzle(outer, inner)
            if offset is None:
                print(f"[{username}] Slide: solver returned no offset")
                return False
            print(f"[{username}] Slide puzzle solved -> offset {offset:.0f}px")

            slider = page.locator('[data-e2e*="slider"], .slider, input[type=range], div[role="slider"], [class*="drag"]').first
            if slider.count() == 0:
                slider = page.locator('div[style*="cursor"], [class*="button"], circle, [class*="handle"]').first
            if slider.count() == 0:
                print(f"[{username}] Slide: no slider handle found")
                return False

            box = slider.bounding_box(timeout=5000)
            if not box:
                return False
            start_x = box["x"] + box["width"] / 2
            start_y = box["y"] + box["height"] / 2

            page.mouse.move(start_x, start_y)
            page.mouse.down()
            time.sleep(0.15)
            steps = max(10, int(offset / 15))
            for i in range(steps):
                cx = start_x + (offset * (i + 1) / steps) + random.uniform(-1.5, 1.5)
                page.mouse.move(cx, start_y, steps=1)
                time.sleep(0.02)
            page.mouse.up()
            time.sleep(1.2)

            for btn_text in ["Verify", "Submit", "Confirm", "Done"]:
                try:
                    btn = page.locator(f'button:has-text("{btn_text}")').first
                    if btn.count() > 0 and btn.is_visible():
                        btn.click(timeout=4000)
                        time.sleep(1.5)
                        break
                except Exception:
                    pass
            time.sleep(2)
            return True
        except Exception as e:
            print(f"[{username}] Slide error: {str(e)[:70]}")
            return False

    return False

def handle_captcha_if_present(page, username=""):
    if page is None:
        return True
    try:
        captcha_type = _detect_tiktok_captcha(page)
        if not captcha_type:
            return True
        print(f"[{username}] Captcha detected — attempting solve...")
        solved = solve_tiktok_captcha(page, username)
        if solved:
            time.sleep(3)
            if _detect_tiktok_captcha(page) is None:
                print(f"[{username}] Captcha solved")
                return True
        return False
    except Exception as e:
        print(f"[{username}] handle_captcha error: {e}")
        return False

# === POPUP HANDLERS ===
def _dismiss_blockers(page, username=""):
    blockers = [
        'button[data-e2e="cookie_banner_button"]',
        'button:has-text("Not now")',
        'button:has-text("Skip")',
        '[class*="joyride"] button',
        '[class*="modal"] button[aria-label="Close"]',
        'button:has-text("Maybe later")',
        'button:has-text("Got it")',
        'button:has-text("Turn on")',
        'button:has-text("Dismiss")',
    ]
    for sel in blockers:
        try:
            blk = page.locator(sel).first
            if blk.count() > 0 and blk.is_visible():
                blk.click(timeout=1500, force=True)
                print(f"[{username}] dismissed: {sel}")
                time.sleep(0.4)
        except Exception:
            pass

def handle_content_check_dialog(page, username=""):
    if page is None:
        return False
    try:
        for _ in range(10):
            possibles = page.locator('div[role="dialog"], [aria-modal="true"]')
            dialog_found = False
            for i in range(min(possibles.count(), 6)):
                try:
                    d = possibles.nth(i)
                    if d.count() > 0 and d.is_visible():
                        txt = (d.inner_text(timeout=900) or "").lower()
                        if "turn on" in txt and "content" in txt:
                            dialog_found = True
                            break
                        turn_btns = d.locator('button:has-text("Turn on")')
                        if turn_btns.count() > 0:
                            dialog_found = True
                            break
                except: pass
                if dialog_found: break
            if dialog_found: break
            time.sleep(random.uniform(0.5, 1.0))
        
        if not dialog_found:
            try:
                body_txt = (page.inner_text("body", timeout=1500) or "").lower()
                if "turn on" in body_txt and "content" in body_txt:
                    dialog_found = True
            except: pass
        
        if not dialog_found:
            return False
        
        end = time.time() + 22
        clicked = False
        while time.time() < end and not clicked:
            for txt in ["Turn on", "Turn On", "TURN ON", "turn on"]:
                try:
                    btn = page.locator(f'button:has-text("{txt}")').first
                    if btn.count() > 0 and btn.is_visible():
                        btn.click(timeout=2200, force=True, no_wait_after=True)
                        clicked = True
                        break
                except: pass
                if clicked: break
            if clicked: break
            
            try:
                btns = page.locator('button, [role="button"], div[role="button"]')
                for k in range(min(btns.count(), 10)):
                    b = btns.nth(k)
                    if b.count() > 0 and b.is_visible():
                        bt = (b.inner_text(timeout=600) or "").lower().strip()
                        if "turn" in bt and ("on" in bt or len(bt) < 15):
                            b.click(timeout=1800, force=True, no_wait_after=True)
                            clicked = True
                            break
            except: pass
            if clicked: break
            
            try:
                result = page.evaluate('''
                    const all = Array.from(document.querySelectorAll('button, [role="button"]'));
                    let target = all.find(b => {
                        const t = (b.innerText || b.textContent || "").toLowerCase().trim();
                        return t.includes("turn on") || (t.includes("turn") && t.includes("on"));
                    });
                    if (target) { target.click(); return "clicked"; }
                    return "no-match";
                ''')
                if result == "clicked":
                    clicked = True
            except: pass
            
            if not clicked:
                time.sleep(random.uniform(0.45, 0.95))
        
        if clicked:
            time.sleep(2.5)
            return True
        return False
    except Exception as e:
        print(f"[{username}] Content dialog error: {str(e)[:80]}")
        return False

# === GMAIL OTP FETCHER ===
def get_gmail_otp(gmail_user="zeroghaith2012@gmail.com", gmail_pass=None, timeout=120):
    if not gmail_pass:
        gmail_pass = os.environ.get("GMAIL_APP_PASSWORD")
    if not gmail_pass:
        print("[OTP] No Gmail password configured")
        return None
    
    try:
        import imaplib, email
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(gmail_user, gmail_pass)
        mail.select("inbox")
        
        start = time.time()
        while time.time() - start < timeout:
            _, data = mail.search(None, '(UNSEEN FROM "noreply@tiktok.com")')
            ids = data[0].split()
            if ids:
                latest = ids[-1]
                _, msg_data = mail.fetch(latest, "(RFC822)")
                raw = msg_data[0][1]
                msg = email.message_from_bytes(raw)
                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            body = part.get_payload(decode=True).decode()
                            break
                else:
                    body = msg.get_payload(decode=True).decode()
                
                import re
                match = re.search(r'\b\d{6}\b', body)
                if match:
                    return match.group(0)
            time.sleep(3)
        return None
    except Exception as e:
        print(f"[OTP] Gmail fetch error: {e}")
        return None

# === TIKTOK SIGNUP FLOW ===
def signup_tiktok(username, email, password, dob, tor_port_offset=0, auto_password=False, auto_dob=False):
    """Full TikTok account creation with Tor, captcha solving, and OTP handling."""
    log(f"[{username}] === STARTING TIKTOK SIGNUP ===")

    # Auto-generate credentials when requested / missing
    if auto_password or not password:
        password = generate_password()
        log(f"[{username}] Auto-generated password: {password}")
    if auto_dob or not dob:
        dob = generate_dob()
        log(f"[{username}] Auto-selected DOB: {dob}")
    
    # Start Tor (falls back to None when the tor binary is unavailable)
    tor_proc, socks_port, control_port = _start_tor_instance(username, tor_port_offset)
    if tor_proc is None:
        log(f"[{username}] Running WITHOUT Tor proxy (direct connection).")
    else:
        log(f"[{username}] Tor started on SOCKS {socks_port}, Control {control_port}")

    # Start browser (no proxy when Tor is unavailable)
    try:
        session = _start_browser_session(username, tor_socks_port=socks_port)
    except Exception as e:
        log(f"[{username}] FATAL: could not launch browser: {e}")
        return False
    page = session["page"]
    
    try:
        # Go to TikTok signup
        page.goto("https://www.tiktok.com/signup", timeout=30000)
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        time.sleep(3)
        take_screenshot(username)
        
        # Handle initial popups
        _dismiss_blockers(page, username)
        handle_captcha_if_present(page, username)
        
        # Fill email
        try:
            email_input = page.locator('input[type="email"], input[name="email"], input[placeholder*="email" i]').first
            if email_input.count() > 0:
                email_input.wait_for(state="visible", timeout=8000)
                email_input.fill(email)
                time.sleep(random.uniform(0.5, 1.5))
                log(f"[{username}] Email filled")
        except Exception as e:
            log(f"[{username}] Email fill error: {e}")
        
        # Fill password
        try:
            pass_input = page.locator('input[type="password"], input[name="password"]').first
            if pass_input.count() > 0:
                pass_input.wait_for(state="visible", timeout=8000)
                pass_input.fill(password)
                time.sleep(random.uniform(0.5, 1.5))
                log(f"[{username}] Password filled")
        except Exception as e:
            log(f"[{username}] Password fill error: {e}")
        
        # Fill DOB (dropdowns or native date input)
        if dob:
            try:
                if not _fill_dob_selectors(page, dob):
                    # Fallback: native date input
                    dob_input = page.locator('input[type="date"], input[name*="birth" i], input[placeholder*="date" i]').first
                    if dob_input.count() > 0:
                        dob_input.fill(dob)
                log(f"[{username}] DOB filled: {dob}")
                time.sleep(random.uniform(0.5, 1.2))
            except Exception as e:
                log(f"[{username}] DOB fill error: {e}")
        
        # Click signup/submit
        try:
            submit = page.locator('button[type="submit"], button:has-text("Sign up"), button:has-text("Next")').first
            if submit.count() > 0:
                submit.click(timeout=5000)
                time.sleep(2)
        except Exception as e:
            log(f"[{username}] Submit error: {e}")
        
        take_screenshot(username)
        
        # Handle post-submit captchas
        for _ in range(5):
            if _detect_tiktok_captcha(page):
                handle_captcha_if_present(page, username)
                time.sleep(2)
            else:
                break
        
        # Check for phone/email verification
        try:
            body = (page.inner_text("body", timeout=3000) or "").lower()
            if "verify" in body or "code" in body or "6-digit" in body:
                log(f"[{username}] Verification code required")
                # Try auto-fetch from Gmail
                otp = get_gmail_otp(timeout=60)
                if otp:
                    log(f"[{username}] Auto-fetched OTP: {otp}")
                    try:
                        code_input = page.locator('input[type="text"], input[placeholder*="code" i], input[placeholder*="digit" i]').first
                        if code_input.count() > 0:
                            code_input.fill(otp)
                            time.sleep(1)
                            verify_btn = page.locator('button:has-text("Verify"), button:has-text("Submit")').first
                            if verify_btn.count() > 0:
                                verify_btn.click(timeout=5000)
                                time.sleep(3)
                    except Exception as e:
                        log(f"[{username}] OTP submit error: {e}")
                else:
                    log(f"[{username}] Waiting for manual OTP entry...")
                    # Signal to dashboard that manual OTP is needed
                    for _ in range(60):  # Wait up to 5 minutes
                        time.sleep(5)
                        take_screenshot(username)
                        # Check if page advanced
                        new_body = (page.inner_text("body", timeout=2000) or "").lower()
                        if "verify" not in new_body and "code" not in new_body:
                            log(f"[{username}] Page advanced - OTP was entered manually")
                            break
        except Exception as e:
            log(f"[{username}] Verification check error: {e}")
        
        # Handle content check dialog
        handle_content_check_dialog(page, username)
        
        # Final screenshot
        take_screenshot(username)
        log(f"[{username}] Signup flow complete. Current URL: {page.url}")
        
        # Save session cookies
        try:
            cookies = session["context"].cookies()
            cookie_path = os.path.join(DOWNLOADS_DIR, f"cookies_{username}.json")
            with open(cookie_path, "w") as f:
                json.dump(cookies, f)
            log(f"[{username}] Saved {len(cookies)} cookies")
        except Exception as e:
            log(f"[{username}] Cookie save error: {e}")
        
        return True
        
    except Exception as e:
        log(f"[{username}] FATAL SIGNUP ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        take_screenshot(username)

# === LIVE CAM THREAD ===
def start_live_cam(username):
    """Start a dedicated thread that screenshots every 2 seconds."""
    def cam_loop():
        while workers.get(username):
            try:
                take_screenshot(username)
            except Exception:
                pass
            time.sleep(2)
    
    t = threading.Thread(target=cam_loop, daemon=True)
    t.start()
    return t

# === WORKER CONTROL ===
def start_worker(username, email, password, dob, tor_offset=0, auto_password=False, auto_dob=False):
    if username in workers:
        return False
    workers[username] = {"running": True}
    thread = threading.Thread(
        target=_worker_thread,
        args=(username, email, password, dob, tor_offset, auto_password, auto_dob),
        daemon=True
    )
    thread.start()
    return True

def _worker_thread(username, email, password, dob, tor_offset, auto_password, auto_dob):
    start_live_cam(username)
    try:
        success = signup_tiktok(username, email, password, dob, tor_offset, auto_password, auto_dob)
        workers[username]["success"] = success
    except Exception as e:
        log(f"[{username}] Worker error: {e}")
        workers[username]["error"] = str(e)
    finally:
        # Cleanup
        if username in browser_sessions:
            try:
                browser_sessions[username]["context"].close()
                browser_sessions[username]["browser"].close()
                browser_sessions[username]["pw"].stop()
            except: pass
            del browser_sessions[username]

def stop_worker(username):
    if username in workers:
        workers[username]["running"] = False
        workers.pop(username, None)
    if username in browser_sessions:
        try:
            browser_sessions[username]["context"].close()
            browser_sessions[username]["browser"].close()
            browser_sessions[username]["pw"].stop()
        except: pass
        del browser_sessions[username]

# === CREDENTIAL GENERATION ===
def generate_password(length=14):
    """Generate a strong, TikTok-friendly password."""
    lower = "abcdefghjkmnpqrstuvwxyz"
    upper = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    digits = "23456789"
    all_chars = lower + upper + digits
    pwd = [
        random.choice(lower),
        random.choice(upper),
        random.choice(digits),
    ]
    pwd += [random.choice(all_chars) for _ in range(length - 3)]
    random.shuffle(pwd)
    return "".join(pwd)

def generate_username():
    """Generate a TikTok-style handle like 'user.8f3k2'."""
    adjectives = ["cool", "tiny", "lunar", "neon", "vibe", "pixel", "storm",
                  "ghost", "frost", "echo", "nova", "drift", "cyber", "lazy"]
    nouns = ["panda", "wolf", "comet", "tiger", "mango", "raven", "kitty",
             "fox", "ninja", "bot", "star", "moon", "wave", "leaf"]
    base = f"{random.choice(adjectives)}{random.choice(nouns)}"
    suffix = "".join(random.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(4))
    return f"{base}{suffix}"

def generate_dob(min_age=18, max_age=45):
    """Return a random DOB (ISO date) making the user old enough to register."""
    today = datetime.now().date()
    max_birth = today.replace(year=today.year - min_age)
    min_birth = today.replace(year=today.year - max_age)
    span_days = (max_birth - min_birth).days
    birth = min_birth + timedelta(days=random.randint(0, span_days))
    return birth.isoformat()

def _fill_dob_selectors(page, dob):
    """Fill the TikTok date-of-birth UI which uses month/day/year dropdowns."""
    try:
        dt = datetime.strptime(dob, "%Y-%m-%d")
    except Exception:
        return False

    month_names = ["January", "February", "March", "April", "May", "June",
                   "July", "August", "September", "October", "November", "December"]
    month_label = month_names[dt.month - 1]

    selectors_by_field = {
        "month": [
            'select[name="birthMonth"], select[data-e2e*="month" i], select[aria-label*="month" i]',
            'div[role="button"]:has-text("Month"), [data-e2e*="month" i]',
        ],
        "day": [
            'select[name="birthDay"], select[data-e2e*="day" i], select[aria-label*="day" i]',
            'div[role="button"]:has-text("Day"), [data-e2e*="day" i]',
        ],
        "year": [
            'select[name="birthYear"], select[data-e2e*="year" i], select[aria-label*="year" i]',
            'div[role="button"]:has-text("Year"), [data-e2e*="year" i]',
        ],
    }
    values = {
        "month": [str(dt.month), month_label, f"{dt.month:02d}"],
        "day": [str(dt.day), f"{dt.day:02d}"],
        "year": [str(dt.year)],
    }

    filled = 0
    for field, sels in selectors_by_field.items():
        for sel in sels:
            try:
                el = page.locator(sel).first
                if el.count() == 0 or not el.is_visible():
                    continue
                tag = (el.evaluate("e => e.tagName.toLowerCase()") or "")
                if tag == "select":
                    for v in values[field]:
                        try:
                            el.select_option(label=v, timeout=1500)
                            filled += 1
                            break
                        except Exception:
                            pass
                        try:
                            el.select_option(value=v, timeout=1500)
                            filled += 1
                            break
                        except Exception:
                            pass
                else:
                    el.click(timeout=1500)
                    time.sleep(0.4)
                    for v in values[field]:
                        try:
                            opt = page.locator(f'div[role="option"]:has-text("{v}"), li:has-text("{v}"), [role="option"]:has-text("{v}")').first
                            if opt.count() > 0 and opt.is_visible():
                                opt.click(timeout=1500)
                                filled += 1
                                break
                        except Exception:
                            pass
                if filled > 0:
                    time.sleep(0.4)
                break
            except Exception:
                continue
    return filled > 0

# === UTILS ===
def get_screenshot(username):
    return screenshots.get(username)

def get_worker_status(username):
    return workers.get(username, {})

if __name__ == "__main__":
    print("Bot module loaded. Use start_worker() to begin.")
