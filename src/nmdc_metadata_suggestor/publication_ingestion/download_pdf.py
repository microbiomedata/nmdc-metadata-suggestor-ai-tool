import os
import tempfile
from urllib.parse import urlparse
import requests
from curl_cffi import requests as cffi_requests

# Domains known to use Cloudflare JS challenges that require browser-like
# TLS fingerprints.  curl_cffi (via curl-impersonate) is used for these;
# plain ``requests`` would get a 403.
CLOUDFLARE_PROTECTED_DOMAINS = {
    "pubs.acs.org",
}


def _needs_browser_impersonation(url: str) -> bool:
    """Return True if the URL's domain requires browser TLS impersonation."""
    hostname = urlparse(url).hostname or ""
    return hostname in CLOUDFLARE_PROTECTED_DOMAINS


def download_pdf_to_tempfile(url: str) -> str:
    """Download a PDF from *url* to a temporary file and return its path.

    For sites behind Cloudflare JS challenges (e.g. ``pubs.acs.org``),
    ``curl_cffi`` is used to impersonate a Chrome browser's TLS fingerprint,
    which is required to pass the challenge.

    Args:
        url: The URL of the PDF file.

    Returns:
        The path to the downloaded temporary PDF file.

    Raises:
        RuntimeError: If the download fails (HTTP error, network issue, etc.).
    """
    temp_path: str | None = None
    try:
        fd, temp_path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        print(f"Temporary file created at: {temp_path}")

        if _needs_browser_impersonation(url):
            _download_with_cffi(url, temp_path)
        else:
            _download_with_requests(url, temp_path)

        # Sanity-check: make sure we got a real PDF
        with open(temp_path, "rb") as f:
            magic = f.read(5)
        if magic != b"%PDF-":
            raise RuntimeError(
                f"Downloaded file does not appear to be a PDF (header: {magic!r})"
            )

        return temp_path

    except Exception as e:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)
        raise RuntimeError(f"Error during download: {e}") from e

def _download_with_requests(url: str, dest: str) -> None:
    """Download *url* to *dest* using plain requests."""
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    with open(dest, "wb") as f:
        f.write(response.content)

def _download_with_cffi(url: str, dest: str) -> None:
    """Download *url* to *dest* using curl_cffi with Chrome impersonation."""
    resp = cffi_requests.get(url, impersonate="chrome", timeout=60)
    resp.raise_for_status()
    with open(dest, "wb") as f:
        f.write(resp.content)


if __name__ == "__main__":
    # pdf_url = "https://link.springer.com/content/pdf/10.1186/s12859-024-05977-2.pdf"
    pdf_url = "https://pubs.acs.org/doi/pdf/10.1021/acs.jpcb.5c06231"

    try:
        temp_file_path = download_pdf_to_tempfile(pdf_url)
        print(f"PDF successfully downloaded to: {temp_file_path}")
        print(f"File size: {os.path.getsize(temp_file_path):,} bytes")

        # for now, delete the temporary file
        os.remove(temp_file_path)
        print(f"Temporary file {temp_file_path} deleted.")
    except RuntimeError as e:
        print(e)
