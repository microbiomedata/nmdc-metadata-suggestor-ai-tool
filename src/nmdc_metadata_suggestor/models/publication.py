from pydantic import BaseModel, Field, HttpUrl
from typing import Optional, List

class Publication(BaseModel):
    """Model for publication information from DOI."""
    source: Optional[str] = Field(None, description="Source of the pdf link")    
    osti_doi: Optional[str] = Field(None, description="OSTI Digital Object Identifier")
    publication_doi: Optional[str] = Field(None, description="DOI of the publication assocaited with the OSTI record")
    pmid: Optional[str] = Field(None, description="PubMed ID")
    urls: Optional[List[HttpUrl]] = Field(None, description="List of URLs to the publication")
    abstract: Optional[str] = Field(None, description="Publication abstract")