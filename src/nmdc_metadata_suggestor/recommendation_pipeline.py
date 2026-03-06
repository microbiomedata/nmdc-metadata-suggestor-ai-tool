from nmdc_metadata_suggestor.doi_ingestion.main import get_doi_description_or_abstract
from nmdc_metadata_suggestor.llm_client import LLMClient
from nmdc_metadata_suggestor.publication_ingestion.download_pdf import download_pdf_to_tempfile
from nmdc_metadata_suggestor.schema_context import SchemaContextBuilder


def run_recommendation_pipeline(
    doi: str,
    llm_client: LLMClient,
    mixis_extensions: list[str],
    sources: list[str] | None = None,
) -> str:
    """Run the metadata recommendation pipeline with the given prompt.

    Returns:
        The response from the LLM containing the recommended metadata fields.
    """
    # based on the DOI, retrieve the abstract and PDF information from OSTI
    result = get_doi_description_or_abstract(doi=doi, skip_classification=True, sources=sources)
    abstract = result.context if result.context else ""
    publication_links = result.publication_urls or []
    builder = SchemaContextBuilder()
    mixis_schema = builder.format_multi_interface_context(mixis_extensions)
    pdf_files: list[str] | None
    if publication_links:
        pdf_files = []
        for url in publication_links:
            try:
                pdf_path = download_pdf_to_tempfile(str(url))
                pdf_files.append(pdf_path)
            except Exception as e:
                print(f"Error downloading PDF from {url}: {e}")
    else:
        pdf_files = None
    llm_client.add_schema_context(mixis_schema)
    llm_client.add_message(
        role="user",
        text="Utilize the following abstract and PDF content to "
        "inform your metadata field recommendations:\n" + abstract,
        pdf_files=pdf_files,
    )
    response = llm_client.generate()
    return response


if __name__ == "__main__":
    llm_client = LLMClient(access_provider="pnnl")
    mixis_extensions = ["SoilInterface"]
    doi = ["10.1073/pnas.2004192118"]
    recommended_metadata = run_recommendation_pipeline(
        doi=doi[0], llm_client=llm_client, mixis_extensions=mixis_extensions, sources=["osti"]
    )
    print(recommended_metadata)
