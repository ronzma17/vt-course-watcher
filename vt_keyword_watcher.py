import json
import re
import time
from pathlib import Path
from dataclasses import dataclass
from typing import List, Optional, Tuple

from colorama import Fore, Style, init as color_init
from win10toast import ToastNotifier

import smtplib
from email.mime.text import MIMEText
from email.utils import formatdate

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException, WebDriverException, ElementClickInterceptedException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options

# main url
VT_REG_URL = "https://registration.es.cloud.vt.edu/StudentRegistrationSsb/ssb/registration"
DEBUG_KEEP_BROWSER_ON_ERROR = True

# user config
@dataclass
class Config:
    crns: List[str]
    poll_seconds: int = 60
    max_errors_before_restart: int = 8
    keep_page_awake_minutes: int = 10
    notify_repeat: bool = False

# email config
@dataclass
class EmailCfg:
    smtp_server: str
    smtp_port: int
    use_ssl: bool
    use_starttls: bool
    username: str
    password: str
    to: List[str]
    subject_prefix: str = "[VT Seat Alert]"

# load user config
def load_config() -> Config:
    cfg = json.loads(Path("config_keyword.json").read_text(encoding="utf-8"))
    return Config(
        crns=cfg.get("crns", []),
        poll_seconds=cfg.get("poll_seconds", 60),
        max_errors_before_restart=cfg.get("max_errors_before_restart", 8),
        keep_page_awake_minutes=cfg.get("keep_page_awake_minutes", 10),
        notify_repeat=cfg.get("notify_repeat", False),
    )

# load email config
def load_email_cfg() -> EmailCfg:
    e = json.loads(Path("email_config.json").read_text(encoding="utf-8"))
    return EmailCfg(
        smtp_server=e["smtp_server"],
        smtp_port=int(e["smtp_port"]),
        use_ssl=bool(e.get("use_ssl", True)),
        use_starttls=bool(e.get("use_starttls", False)),
        username=e["username"],
        password=e["password"],
        to=e["to"],
        subject_prefix=e.get("subject_prefix", "[VT Seat Alert]")
    )

# selectors
SELECTORS = {
    "keyword_input": [
        (By.XPATH, "//label[contains(.,'Keyword')]/following::input[1]"),
        (By.CSS_SELECTOR, "input[id*='keyword' i], input[name*='keyword' i], input[aria-label*='Keyword' i]"),
    ],
    "search_button": [
        (By.XPATH, "//button[contains(., 'Search') and not(contains(., 'Again'))]"),
        (By.CSS_SELECTOR, "button[id*='search' i], button[name*='search' i]"),
    ],
    "search_again_button": [
        (By.XPATH, "//button[contains(., 'Search Again')]"),
        (By.CSS_SELECTOR, "button[id*='searchAgain' i], button[name*='searchAgain' i]"),
    ],
    "results_rows": [
        (By.XPATH, "//table//tbody//tr"),
        (By.CSS_SELECTOR, "table tbody tr"),
    ],
    "expand_row": [
        (By.XPATH, ".//button[contains(., 'Details') or contains(., 'Section') or contains(., 'More')]"),
        (By.CSS_SELECTOR, "button[id*='detail' i], button[id*='more' i]"),
    ],
    "status_badge": [
        (By.XPATH, ".//*[contains(@class,'status') or contains(@class,'badge') or contains(@class,'label')]"),
        (By.CSS_SELECTOR, "[class*='status' i], [class*='badge' i], [class*='label' i]"),
    ],
    "ok_button": [
        (By.XPATH, "//button[normalize-space()='OK' or normalize-space()='Ok']"),
        (By.XPATH, "//*[self::button or self::a][contains(., 'OK') or contains(., 'Ok')]"),
    ],
    "inactivity_no": [
        (By.XPATH, "//div[contains(@class,'modal') or contains(@class,'dialog') or contains(@class,'alert')][.//text()[contains(.,'inactive') or contains(.,'logout')]]//button[normalize-space()='No']"),
        (By.XPATH, "//button[normalize-space()='No']"),
    ],
    "shim": [
        (By.CSS_SELECTOR, ".notification-center-shim, .shim, .modal-backdrop, .ui-widget-overlay"),
    ],
}

# regex
RGX_OF_SEATS = re.compile(r"(\d+)\s*of\s*(\d+)\s*seats", re.I)
RGX_SEATS_KV = [
    re.compile(r"seats\s*(available|remaining)\s*[:\-]?\s*(\d+)", re.I),
    re.compile(r"\bremaining\s*[:\-]?\s*(\d+)", re.I),
    re.compile(r"\bavailable\s*[:\-]?\s*(\d+)", re.I),
]
RGX_CAP_ENR_REM = re.compile(
    r"(capacity|cap)\s*[:\-]?\s*(\d+).{0,40}?(enrolled|enr)\s*[:\-]?\s*(\d+).{0,40}?(remaining|rem)\s*[:\-]?\s*(\d+)",
    re.I | re.S
)

# open browser
def launch_browser() -> webdriver.Chrome:
    opts = Options()
    opts.add_argument("--start-maximized")
    opts.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    opts.add_experimental_option('useAutomationExtension', False)
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=opts)
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        'source': "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
    })
    return driver

# wait find element
def first_present(driver, wait, candidates, timeout=15):
    last_exc = None
    for by, sel in candidates:
        try:
            elem = wait.until(EC.presence_of_element_located((by, sel)))
            wait.until(EC.visibility_of(elem))
            return elem
        except Exception as e:
            last_exc = e
    raise TimeoutException(str(last_exc) if last_exc else "Element not found")

# find all elements
def find_all_now(driver, candidates):
    for by, sel in candidates:
        elems = driver.find_elements(by, sel)
        if elems:
            return elems
    return []

# clear popups
def clear_overlays(driver, wait, verbose=True):
    changed = False
    try:
        btn_no = None
        for by, sel in SELECTORS["inactivity_no"]:
            els = driver.find_elements(by, sel)
            if els:
                btn_no = els[0]
                break
        if btn_no:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn_no)
            time.sleep(0.05)
            btn_no.click()
            changed = True
            if verbose:
                print(Fore.YELLOW + "inactivity popup closed")
            time.sleep(0.15)
    except Exception:
        pass

    try:
        for by, sel in SELECTORS["ok_button"]:
            ok_buttons = driver.find_elements(by, sel)
            for b in ok_buttons:
                try:
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", b)
                    time.sleep(0.05)
                    b.click()
                    changed = True
                    if verbose:
                        print(Fore.YELLOW + "ok popup closed")
                    time.sleep(0.1)
                except Exception:
                    pass
    except Exception:
        pass

    try:
        for by, sel in SELECTORS["shim"]:
            shims = driver.find_elements(by, sel)
            for s in shims:
                try:
                    style = (s.get_attribute("style") or "").lower()
                    if "display: none" in style:
                        continue
                    driver.execute_script("""
                        try {
                            arguments[0].style.setProperty('display','none','important')
                            arguments[0].style.setProperty('pointer-events','none','important')
                        } catch(e){}
                    """, s)
                    changed = True
                    if verbose:
                        print(Fore.YELLOW + "overlay hidden")
                except Exception:
                    pass
    except Exception:
        pass
    return changed

# safe click
def click_safely(driver, wait, elem):
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", elem)
    time.sleep(0.05)
    clear_overlays(driver, wait, verbose=False)
    try:
        WebDriverWait(driver, 6).until(EC.element_to_be_clickable(elem))
    except Exception:
        pass
    try:
        elem.click()
        return
    except Exception:
        try:
            driver.execute_script("arguments[0].click();", elem)
        except Exception:
            driver.execute_script("window.scrollBy(0, -120);")
            time.sleep(0.1)
            elem.click()

# remove popups
def dismiss_all_notices(driver, wait):
    clear_overlays(driver, wait, verbose=False)

# wait user
def wait_user_login_and_term():
    print(Fore.CYAN + "open browser login and go to keyword page then press enter" + Style.RESET_ALL)
    input()

# get text
def text_plus_attrs(elem) -> str:
    chunks = []
    try:
        t = (elem.text or "").strip()
        if t:
            chunks.append(t)
    except Exception:
        pass
    try:
        inner = elem.get_attribute("innerText") or ""
        inner = inner.strip()
        if inner and inner not in chunks:
            chunks.append(inner)
    except Exception:
        pass
    for attr in ["title", "aria-label", "data-original-title", "data-title"]:
        try:
            v = elem.get_attribute(attr)
            if v:
                v = v.strip()
                if v and v not in chunks:
                    chunks.append(v)
        except Exception:
            pass
    return " | ".join(chunks)

# read seat number
def parse_seats_from_any_text(text: str) -> Optional[int]:
    t = (text or "").lower()
    m = RGX_OF_SEATS.search(t)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            pass
    for rgx in RGX_SEATS_KV:
        m2 = rgx.search(t)
        if m2:
            for g in m2.groups()[::-1]:
                if g and str(g).isdigit():
                    return int(g)
    m3 = RGX_CAP_ENR_REM.search(t)
    if m3:
        try:
            return int(m3.group(6))
        except Exception:
            pass
    if "full" in t or "closed" in t:
        return 0
    if "open" in t:
        return 1
    return None

# parse each row
def parse_row_seats(driver, row) -> Tuple[Optional[int], str]:
    raw_parts = []
    raw_parts.append(text_plus_attrs(row))
    try:
        tds = row.find_elements(By.CSS_SELECTOR, "td")
        for td in tds:
            raw_parts.append(text_plus_attrs(td))
    except Exception:
        pass
    try:
        badges = row.find_elements(*SELECTORS["status_badge"][0]) or row.find_elements(*SELECTORS["status_badge"][1])
        for b in badges:
            raw_parts.append(text_plus_attrs(b))
    except Exception:
        pass
    blob = " | ".join([p for p in raw_parts if p])
    seats = parse_seats_from_any_text(blob)
    if seats is not None:
        return seats, blob
    return None, blob

# check one course
def check_one_crn(driver, wait, crn: str) -> Tuple[Optional[int], str]:
    dismiss_all_notices(driver, wait)
    keyword = first_present(driver, wait, SELECTORS["keyword_input"], timeout=15)
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", keyword)
    try:
        keyword.clear()
    except Exception:
        pass
    keyword.send_keys(crn)
    btn = first_present(driver, wait, SELECTORS["search_button"], timeout=15)
    click_safely(driver, wait, btn)
    time.sleep(0.5)
    rows = find_all_now(driver, SELECTORS["results_rows"])
    if not rows:
        WebDriverWait(driver, 10).until(lambda d: len(find_all_now(d, SELECTORS["results_rows"])) > 0)
        rows = find_all_now(driver, SELECTORS["results_rows"])
    for row in rows:
        row_blob = text_plus_attrs(row).lower()
        if crn.lower() in row_blob:
            seats, raw = parse_row_seats(driver, row)
            return seats, raw
    return None, ""

# send email
def send_email(email_cfg: EmailCfg, subject: str, body: str):
    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = email_cfg.username
    msg["To"] = ", ".join(email_cfg.to)
    msg["Subject"] = f"{email_cfg.subject_prefix} {subject}"
    msg["Date"] = formatdate(localtime=True)
    if email_cfg.use_ssl:
        with smtplib.SMTP_SSL(email_cfg.smtp_server, email_cfg.smtp_port) as server:
            server.login(email_cfg.username, email_cfg.password)
            server.sendmail(email_cfg.username, email_cfg.to, msg.as_string())
    else:
        with smtplib.SMTP(email_cfg.smtp_server, email_cfg.smtp_port) as server:
            if email_cfg.use_starttls:
                server.starttls()
            server.login(email_cfg.username, email_cfg.password)
            server.sendmail(email_cfg.username, email_cfg.to, msg.as_string())

# main run
def main():
    color_init(autoreset=True)
    toaster = ToastNotifier()
    cfg = load_config()
    email_cfg = load_email_cfg()
    print(Fore.GREEN + "vt seat watcher start")
    print(Fore.GREEN + "CRNs " + ", ".join(cfg.crns))
    driver = launch_browser()
    wait = WebDriverWait(driver, 20)
    try:
        driver.get(VT_REG_URL)
        print(Fore.CYAN + "login then go to keyword search page then press enter" + Style.RESET_ALL)
        input()
        last_seen = {c: None for c in cfg.crns}
        while True:
            clear_overlays(driver, wait, verbose=False)
            for crn in cfg.crns:
                seats, raw = check_one_crn(driver, wait, crn)
                if seats is None:
                    print(Fore.YELLOW + f"[{crn}] seat not found")
                else:
                    print(Fore.WHITE + f"[{crn}] seats {seats}")
                prev = last_seen.get(crn)
                if seats is not None and seats > 0 and (cfg.notify_repeat or prev is None or prev <= 0):
                    subject = f"CRN {crn} seat open {seats}"
                    body = f"CRN {crn} now has {seats} open seats\n{raw}"
                    try:
                        send_email(email_cfg, subject, body)
                        print(Fore.CYAN + "email sent")
                    except Exception:
                        print(Fore.RED + "email failed")
                    try:
                        toaster.show_toast("VT Seat Alert", subject, duration=8, threaded=True)
                    except Exception:
                        pass
                last_seen[crn] = seats
                time.sleep(cfg.poll_seconds)
    finally:
        if not DEBUG_KEEP_BROWSER_ON_ERROR:
            try:
                driver.quit()
            except Exception:
                pass

if __name__ == "__main__":
    main()
