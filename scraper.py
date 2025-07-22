import requests
import json
import os
import time
import re
import logging
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse
import threading

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException
from bs4 import BeautifulSoup
import chromedriver_autoinstaller
import undetected_chromedriver as uc
from tqdm import tqdm
import glob

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

@dataclass
class DownloadConfig:
    """Configuration class for download settings."""
    download_dir: str = "downloads"
    username: Optional[str] = None
    password: Optional[str] = None
    flaresolverr_host: str = "localhost"
    flaresolverr_port: int = 8191
    proxy: Optional[str] = None
    use_undetected: bool = False
    max_timeout: int = 60000
    retry_count: int = 3
    download_timeout: int = 600
    poll_interval: float = 0.5

@dataclass
class FlareSolverrResult:
    """Result from FlareSolverr request."""
    success: bool
    html: Optional[str] = None
    cookies: Optional[list] = None
    user_agent: Optional[str] = None
    error: Optional[str] = None

class HLTVDownloader:
    """Improved HLTV demo file downloader with better error handling and structure."""
    
    DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
    TEMP_EXTENSIONS = [".crdownload", ".part", ".tmp"]
    DEMO_EXTENSIONS = [".rar", ".zip", ".dem", ".7z"]
    
    def __init__(self, config: DownloadConfig):
        self.config = config
        self.download_dir = Path(config.download_dir).resolve()
        self._setup_download_directory()
        self._install_chromedriver()
        
    def _setup_download_directory(self) -> None:
        """Create download directory if it doesn't exist."""
        self.download_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Download directory: {self.download_dir}")
        
    def _install_chromedriver(self) -> None:
        """Install or update ChromeDriver."""
        try:
            chromedriver_autoinstaller.install()
            logger.info("ChromeDriver installed/updated successfully")
        except Exception as e:
            logger.warning(f"Failed to auto-install ChromeDriver: {e}")

    def get_flaresolverr_solution(self, url: str) -> FlareSolverrResult:
        """Use FlareSolverr to bypass Cloudflare protection."""
        flaresolverr_url = f"http://{self.config.flaresolverr_host}:{self.config.flaresolverr_port}/v1"
        headers = {"Content-Type": "application/json"}
        payload = {
            "cmd": "request.get",
            "url": url,
            "maxTimeout": self.config.max_timeout
        }
        
        if self.config.proxy:
            payload["proxy"] = {"url": self.config.proxy}

        for attempt in range(self.config.retry_count):
            try:
                logger.info(f"FlareSolverr attempt {attempt + 1}/{self.config.retry_count} for {url}")
                response = requests.post(
                    flaresolverr_url, 
                    headers=headers, 
                    json=payload, 
                    timeout=self.config.max_timeout/1000 + 10
                )
                
                if response.status_code == 200:
                    result = response.json()
                    if result.get("status") == "ok":
                        logger.info(f"FlareSolverr succeeded for {url}")
                        return FlareSolverrResult(
                            success=True,
                            html=result["solution"]["response"],
                            cookies=result["solution"]["cookies"],
                            user_agent=result["solution"]["userAgent"]
                        )
                    else:
                        error_msg = result.get('message', 'Unknown error')
                        logger.warning(f"FlareSolverr attempt {attempt + 1} failed: {error_msg}")
                else:
                    logger.warning(f"FlareSolverr HTTP error {response.status_code}")
                    
            except requests.exceptions.RequestException as e:
                logger.error(f"FlareSolverr network error on attempt {attempt + 1}: {e}")
            except Exception as e:
                logger.error(f"FlareSolverr unexpected error on attempt {attempt + 1}: {e}")
                
            if attempt < self.config.retry_count - 1:
                time.sleep(5)
                
        return FlareSolverrResult(success=False, error="Max retries exceeded")

    def _find_download_url_from_html(self, html: str) -> Tuple[Optional[str], Optional[str]]:
        """Extract download URL and filename from HTML content."""
        soup = BeautifulSoup(html, "html.parser")
        download_url = None
        expected_filename = None
        
        # Check for meta-refresh redirect
        meta_refresh = soup.find("meta", {"http-equiv": "refresh"})
        if meta_refresh and meta_refresh.get("content"):
            content = meta_refresh.get("content")
            if "url=" in content.lower():
                download_url = content.split("url=")[-1].strip()
                logger.info(f"Found meta-refresh URL: {download_url}")
                expected_filename = self._extract_filename_from_url(download_url)

        # Check for JavaScript redirects
        if not download_url:
            scripts = soup.find_all("script")
            for script in scripts:
                if script.string and "window.location" in script.string:
                    patterns = [
                        r"window\.location\s*=\s*['\"](.*?)['\"]\s*;",
                        r"window\.location\.href\s*=\s*['\"](.*?)['\"]\s*;",
                        r"location\.href\s*=\s*['\"](.*?)['\"]\s*;"
                    ]
                    
                    for pattern in patterns:
                        match = re.search(pattern, script.string)
                        if match and any(match.group(1).endswith(ext) for ext in self.DEMO_EXTENSIONS):
                            download_url = match.group(1)
                            expected_filename = self._extract_filename_from_url(download_url)
                            logger.info(f"Found JavaScript redirect URL: {download_url}")
                            break
                    if download_url:
                        break

        return download_url, expected_filename

    def _extract_filename_from_url(self, url: str) -> Optional[str]:
        """Extract filename from URL."""
        try:
            parsed = urlparse(url)
            filename = os.path.basename(parsed.path)
            return filename if filename else None
        except Exception as e:
            logger.warning(f"Failed to extract filename from URL {url}: {e}")
            return None

    def _setup_driver(self, user_agent: Optional[str] = None) -> webdriver.Chrome:
        """Setup and configure Chrome WebDriver."""
        effective_user_agent = user_agent or self.DEFAULT_USER_AGENT
        
        if self.config.use_undetected:
            options = uc.ChromeOptions()
            options.add_argument(f"user-agent={effective_user_agent}")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-gpu")
            options.add_argument("--disable-notifications")
            options.add_argument("--disable-extensions")
            options.add_argument("--window-size=1920,1080")
            
            try:
                driver = uc.Chrome(options=options, version_main=138)
            except Exception as e:
                logger.warning(f"Failed to create undetected Chrome driver: {e}")
                # Fallback to regular Chrome
                return self._setup_regular_driver(effective_user_agent)
        else:
            driver = self._setup_regular_driver(effective_user_agent)
            
        driver.set_page_load_timeout(30)
        driver.set_script_timeout(30)
        return driver

    def _setup_regular_driver(self, user_agent: str) -> webdriver.Chrome:
        """Setup regular Chrome WebDriver."""
        options = Options()
        options.add_argument(f"user-agent={user_agent}")
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
            "download.default_directory": str(self.download_dir),
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": True,
            "profile.default_content_settings.popups": 0,
            "profile.default_content_setting_values.automatic_downloads": 1,
        }
        options.add_experimental_option("prefs", prefs)
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        
        return webdriver.Chrome(options=options)

    def _set_cookies(self, driver: webdriver.Chrome, cookies: list, base_url: str) -> None:
        """Set cookies from FlareSolverr in the WebDriver."""
        try:
            parsed_url = urlparse(base_url)
            base_domain = f"{parsed_url.scheme}://{parsed_url.netloc}"
            driver.get(base_domain)
            
            for cookie in cookies:
                try:
                    driver.add_cookie({
                        "name": cookie["name"],
                        "value": cookie["value"],
                        "domain": cookie.get("domain", f".{parsed_url.netloc}"),
                        "path": cookie.get("path", "/")
                    })
                except Exception as e:
                    logger.warning(f"Failed to set cookie {cookie.get('name', 'unknown')}: {e}")
                    
            logger.info(f"Set {len(cookies)} cookies")
        except Exception as e:
            logger.error(f"Failed to set cookies: {e}")

    def monitor_download(self, expected_filename: Optional[str] = None) -> Optional[Path]:
        """Monitor download directory with progress tracking."""
        logger.info(f"Monitoring download directory: {self.download_dir}")
        start_time = time.time()
        pbar = None
        last_size = 0
        monitored_file = None

        while time.time() - start_time < self.config.download_timeout:
            files = list(self.download_dir.glob("*"))
            
            # Find the most relevant file to monitor
            for file_path in files:
                if not file_path.is_file():
                    continue
                    
                filename = file_path.name
                
                # Priority: expected filename > temp files > any demo files
                if expected_filename and expected_filename in filename:
                    monitored_file = file_path
                    break
                elif any(filename.endswith(ext) for ext in self.TEMP_EXTENSIONS):
                    monitored_file = file_path
                    break
                elif any(filename.endswith(ext) for ext in self.DEMO_EXTENSIONS):
                    monitored_file = file_path

            if monitored_file:
                try:
                    file_size = monitored_file.stat().st_size
                    
                    # Initialize progress bar if needed
                    if pbar is None:
                        pbar = tqdm(
                            total=None, 
                            desc=f"Downloading {monitored_file.name}", 
                            unit="B", 
                            unit_scale=True, 
                            leave=True
                        )
                    
                    # Check if it's a temporary file (still downloading)
                    if any(str(monitored_file).endswith(ext) for ext in self.TEMP_EXTENSIONS):
                        pbar.update(file_size - last_size)
                        last_size = file_size
                    else:
                        # Download completed
                        pbar.n = file_size
                        pbar.set_description(f"Download complete: {monitored_file.name}")
                        pbar.close()
                        logger.info(f"Download completed: {monitored_file}")
                        return monitored_file
                        
                except FileNotFoundError:
                    logger.debug(f"File {monitored_file} disappeared, continuing...")
                    last_size = 0
                    if pbar:
                        pbar.close()
                        pbar = None
                    monitored_file = None
                except Exception as e:
                    logger.error(f"Error monitoring file {monitored_file}: {e}")

            time.sleep(self.config.poll_interval)

        if pbar:
            pbar.close()
        logger.warning("Download monitoring timed out or no file detected")
        return None

    def download_demo(self, url: str) -> Optional[Path]:
        """Main method to download a demo file from HLTV."""
        logger.info(f"Starting download process for: {url}")
        
        # Try FlareSolverr first
        flaresolverr_result = self.get_flaresolverr_solution(url)
        
        if not flaresolverr_result.success and not self.config.use_undetected:
            logger.error(f"Failed to bypass Cloudflare: {flaresolverr_result.error}")
            return None

        # Setup WebDriver
        user_agent = flaresolverr_result.user_agent if flaresolverr_result.success else None
        driver = self._setup_driver(user_agent)
        
        try:
            # Set cookies if available
            if flaresolverr_result.success and flaresolverr_result.cookies:
                self._set_cookies(driver, flaresolverr_result.cookies, url)

            # Navigate to the download URL
            logger.info(f"Navigating to: {url}")
            driver.get(url)
            
            # Wait for page to load
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            
            # Give some time for potential JavaScript redirects or downloads to start
            time.sleep(3)
            
            # Try to find download URL from page content
            download_url, expected_filename = self._find_download_url_from_html(driver.page_source)
            
            if download_url:
                logger.info(f"Found download URL: {download_url}")
                # Navigate to the actual download URL if needed
                if not download_url.startswith('http'):
                    download_url = urljoin(url, download_url)
                driver.get(download_url)
                time.sleep(2)

            # Start monitoring download
            logger.info("Starting download monitoring...")
            downloaded_file = self.monitor_download(expected_filename)
            
            return downloaded_file

        except TimeoutException:
            logger.error("Page load timeout")
            return None
        except WebDriverException as e:
            logger.error(f"WebDriver error: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            return None
        finally:
            try:
                driver.quit()
            except Exception as e:
                logger.warning(f"Error closing driver: {e}")

def main():
    """Example usage of the improved HLTV downloader."""
    config = DownloadConfig(
        download_dir="downloads",
        username="your_username",  # Replace with actual username
        password="your_password",  # Replace with actual password
        proxy=None,  # Replace with "http://username:password@host:port" if needed
        use_undetected=False,  # Set to True if FlareSolverr fails
        download_timeout=600,  # 10 minutes timeout
        retry_count=3
    )
    
    downloader = HLTVDownloader(config)
    url = "https://www.hltv.org/download/demo/98547"
    
    downloaded_file = downloader.download_demo(url)
    
    if downloaded_file:
        logger.info(f"Successfully downloaded: {downloaded_file}")
        logger.info(f"File size: {downloaded_file.stat().st_size / (1024*1024):.2f} MB")
    else:
        logger.error("Download failed")

if __name__ == "__main__":
    main()
