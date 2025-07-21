import requests
import json
import os
import time
import re
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
import chromedriver_autoinstaller
import undetected_chromedriver as uc

def get_flaresolverr_solution(url, flaresolverr_host="localhost", flaresolverr_port=8191, max_timeout=60000, proxy=None, retry_count=3):
    """Use FlareSolverr to bypass Cloudflare and get cookies, user-agent, and HTML."""
    flaresolverr_url = f"http://{flaresolverr_host}:{flaresolverr_port}/v1"
    headers = {"Content-Type": "application/json"}
    payload = {
        "cmd": "request.get",
        "url": url,
        "maxTimeout": max_timeout
    }
    if proxy:
        payload["proxy"] = {"url": proxy}

    for attempt in range(retry_count):
        try:
            response = requests.post(flaresolverr_url, headers=headers, json=payload, timeout=max_timeout/1000 + 10)
            if response.status_code == 200:
                result = response.json()
                if result.get("status") == "ok":
                    print(f"FlareSolverr succeeded for {url}")
                    return {
                        "success": True,
                        "html": result["solution"]["response"],
                        "cookies": result["solution"]["cookies"],
                        "user_agent": result["solution"]["userAgent"]
                    }
                else:
                    print(f"FlareSolverr attempt {attempt + 1} failed: {result.get('message')}")
            else:
                print(f"FlareSolverr HTTP error {response.status_code}")
        except Exception as e:
            print(f"FlareSolverr error on attempt {attempt + 1}: {str(e)}")
        if attempt < retry_count - 1:
            time.sleep(5)
    return {"success": False, "error": "Max retries exceeded"}

def get_materials_data(url, download_dir="downloads", username=None, password=None, flaresolverr_host="localhost", flaresolverr_port=8191, proxy=None, use_undetected=False):
    # Ensure download directory exists
    if not os.path.exists(download_dir):
        os.makedirs(download_dir)

    # Install/update ChromeDriver
    chromedriver_autoinstaller.install()

    # Try FlareSolverr first
    print(f"Requesting FlareSolverr for {url}")
    flaresolverr_result = get_flaresolverr_solution(url, flaresolverr_host, flaresolverr_port, proxy=proxy)
    
    if not flaresolverr_result["success"] and not use_undetected:
        print(f"Failed to bypass Cloudflare: {flaresolverr_result['error']}")
        return
    elif flaresolverr_result["success"]:
        cookies = flaresolverr_result["cookies"]
        user_agent = flaresolverr_result["user_agent"]
        html = flaresolverr_result["html"]
    else:
        cookies, user_agent, html = [], "", None

    # Configure Chrome options
    if use_undetected:
        options = uc.ChromeOptions()
        options.add_argument(f"user-agent={user_agent}" if user_agent else "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-notifications")
        options.add_argument("--disable-extensions")
        options.add_argument("--window-size=1920,1080")
        driver = uc.Chrome(options=options, version_main=138)  # Match Chrome version
    else:
        options = Options()
        options.add_argument(f"user-agent={user_agent}" if user_agent else "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36")
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--enable-unsafe-swiftshader")
        options.add_argument("--disable-notifications")
        options.add_argument("--disable-extensions")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-blink-features=AutomationControlled")
        # Set download preferences
        prefs = {
            "download.default_directory": os.path.abspath(download_dir),
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": True
        }
        options.add_experimental_option("prefs", prefs)
        driver = webdriver.Chrome(options=options)

    driver.set_page_load_timeout(30)
    driver.set_script_timeout(30)

    try:
        # Set FlareSolverr cookies if available
        if cookies:
            driver.get("https://www.hltv.org")  # Set cookies on domain
            for cookie in cookies:
                driver.add_cookie({
                    "name": cookie["name"],
                    "value": cookie["value"],
                    "domain": cookie.get("domain", ".hltv.org"),
                    "path": cookie.get("path", "/")
                })

        # Navigate to the download URL
        print(f"Navigating to: {url}")
        driver.get(url)
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(120)  # Wait for JavaScript redirects

        # Check for redirect via current_url
        final_url = url
        print(f"Current URL after redirect: {final_url}")

        # Parse page source for meta-refresh or JavaScript redirect
        download_url = None
        soup = BeautifulSoup(driver.page_source if not html else html, "html.parser")
        meta_refresh = soup.find("meta", {"http-equiv": "refresh"})
        if meta_refresh and meta_refresh.get("content"):
            content = meta_refresh.get("content")
            if "url=" in content.lower():
                download_url = content.split("url=")[-1].strip()
                print(f"Found meta-refresh URL: {download_url}")

        scripts = soup.find_all("script")
        for script in scripts:
            if script.string and "window.location" in script.string:
                match = re.search(r"window\.location\s*=\s*['\"](.*?)['\"]", script.string)
                if match and match.group(1).endswith(".rar"):
                    download_url = match.group(1)
                    print(f"Found JavaScript redirect URL: {download_url}")
                    break

    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        driver.quit()

# Example usage
url = "https://www.hltv.org/download/demo/98531"
get_materials_data(
    url,
    download_dir="downloads",
    username="your_username",  # Replace with your HLTV username
    password="your_password",  # Replace with your HLTV password
    proxy=None,  # Replace with "http://username:password@host:port" if using a proxy
    use_undetected=False  # Set to True if FlareSolverr fails
)
