from nmdc_metadata_suggestor.llm_client import LLMClient
from nmdc_metadata_suggestor.schema_context import SchemaContextBuilder
from nmdc_metadata_suggestor.doi_ingestion.main import get_doi_description_or_abstract
from nmdc_metadata_suggestor.publication_ingestion.download_pdf import download_pdf_to_tempfile

def run_recommendation_pipeline(doi: str, llm_client: LLMClient, mixis_extensions:list[str]) -> str:
    """Run the metadata recommendation pipeline with the given prompt.

    Returns:
        The response from the LLM containing the recommended metadata fields.
    """
    # based on the DOI, retrieve the abstract and PDF information from OSTI
    result = get_doi_description_or_abstract(doi=doi, skip_classification=True)
    abstract = result.context if result.context else ""
    publication_links = result.publication_urls
    builder = SchemaContextBuilder()
    mixis_schema = builder.format_multi_interface_context(mixis_extensions)
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
    # abstract = (
    #     "The conversion of lignocellulosic biomass into biofuels and bioproducts by "
    #     "microbial biorefineries is central to a sustainable chemical industry. "
    #     "Zymomonas mobilis is one such biorefinery chassis and is resistant to ethanol "
    #     "stress, leading to its use in biomass conversion to biofuels and bioproducts. "
    #     "However, Z. mobilis growth is often inhibited by organic acids, aldehydes, "
    #     "alcohols, ketones, and amides found in biomass hydrolysate. "
    #     "The resulting slow growth inhibits production and as a result drives up the "
    #     "price for the resulting products. "
    #     "One hypothesis is that these molecules interact with or disrupt the bacterial "
    #     "membrane, triggering stress responses and hindering growth. "
    #     "To test this hypothesis at the molecular level, we employ all-atom molecular "
    #     "dynamics (MD) simulations to investigate lignocellulose-derived "
    #     "small molecules and their impact on a biologically relevant Z. mobilis "
    #     "membrane model. "
    #     "Simulations were conducted across a range of inhibitor concentrations from 0 "
    #     "to 2.5 mol %, analyzing key membrane properties such as area per lipid (APL), "
    #     "membrane thickness, lipid-order parameter (−SCH), lateral diffusion coefficient "
    #     "(Dxy), and permeability coefficient (Pm). "
    #     "From simulation, we observed altered membrane structure and dynamics at these "
    #     "modest small molecule concentrations commonly found in hydrolysates. "
    #     "Generally, the membranes become thinner, with a higher area per lipid and "
    #     "lower-order parameter as the small molecule concentration increases. "
    #     "These trends are stronger for more hydrophobic molecules with greater "
    #     "hydrophobic bulk, as isobutanol, propanol, and propanoic acid showed greater "
    #     "membrane perturbations as the concentration increased compared to other small "
    #     "molecules. "
    #     "Tracking small molecule distributions directly in our equilibrium simulations "
    #     "allows us to determine concentration-dependent free energy profiles for these "
    #     "molecules. "
    #     "While the trends are noisy, generally the barriers to crossing the membrane "
    #     "decrease as the concentration increases, indicating that the membranes become "
    #     "leakier as small molecule concentrations rise. "
    #     "Comparing between native Z. mobilis membranes with hopanoids and membranes "
    #     "sharing the same phospholipid composition but without hopanoids, hopanoids "
    #     "stabilize and order the membrane for smaller molecules to maintain membrane "
    #     "structure but appear insufficient for larger hydrophobic molecules like "
    #     "isobutanol. "
    #     "These findings provide a mechanistic understanding of how small molecules found "
    #     "in biomass degradation streams interact with the Z. mobilis membrane, offering "
    #     "valuable insights for future strain engineering efforts to optimize biofuel and "
    #     "bioproduct synthesis from biomass feedstocks by highlighting limits to small "
    #     "molecule tolerance. "
    #     "This knowledge can guide the modification of membrane composition to develop "
    #     "more robust microbes, thereby improving microbial survival and yields in "
    #     "industrial contexts."
    # )
    prompt = "Provide recommendations for metadata fields based on the provided information."
    client = LLMClient(access_provider="pnnl")
    client.add_schema_context(mixis_schema)
    client.add_message(role="user", text="Utilize the following abstract and PDF content to inform your metadata field recommendations:\n" + abstract, pdf_files=pdf_files)
    response = client.generate(prompt, abstract=abstract, pdf_files=pdf_files)
    return response


if __name__ == "__main__":
    llm_client = LLMClient(access_provider="pnnl")
    mixis_extensions = ["SoilInterface"]
    doi = ["10.15485/2478895", "10.15485/1729719", "10.15485/1603775"]
    recommended_metadata = run_recommendation_pipeline(doi=doi[0], llm_client=llm_client, mixis_extensions=mixis_extensions)
    print(recommended_metadata)
