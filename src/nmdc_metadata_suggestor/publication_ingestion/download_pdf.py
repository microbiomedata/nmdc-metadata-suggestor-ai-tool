import os
import shutil
import tempfile

import requests


def download_pdf_to_tempfile(url: str) -> str:
    """
    Downloads a PDF file from a given URL to a temporary file.

    Args:
        url (str): The URL of the PDF file.

    Returns:
        str: The path to the temporary PDF file.
    """
    try:
        temp_file = tempfile.NamedTemporaryFile(mode="wb", suffix=".pdf", delete=False)
        print(f"Temporary file created at: {temp_file.name}")

        with requests.get(url, stream=True) as r:
            r.raise_for_status()
            shutil.copyfileobj(r.raw, temp_file)

        temp_file.close()
        return temp_file.name

    except requests.exceptions.RequestException as e:
        if "temp_file" in locals() and not temp_file.closed:
            temp_file.close()
            os.remove(temp_file.name)
        return f"Error during download: {e}"


if __name__ == "__main__":
    pdf_url = "https://link.springer.com/content/pdf/10.1186/s12859-024-05977-2.pdf"
    temp_file_path = download_pdf_to_tempfile(pdf_url)

    if temp_file_path:
        print(f"PDF successfully downloaded to: {temp_file_path}")

        # for now, delete the temporary file
        os.remove(temp_file_path)
        print(f"Temporary file {temp_file_path} deleted.")
