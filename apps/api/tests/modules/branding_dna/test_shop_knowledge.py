import asyncio
import io
import json
import zipfile
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response, UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.branding_dna.model import BrandShopModel
from app.modules.branding_dna.knowledge_model import BrandKnowledgeChunkModel, BrandKnowledgeDocumentModel
from app.modules.branding_dna.knowledge_service import extract_text, chunks, lexical_score
from app.modules.branding_dna.knowledge_router import (
    upload_knowledge, list_knowledge, read_knowledge_chunks,
    review_knowledge, search_knowledge, KnowledgeDecision, KnowledgeQuery,
)

def actor(tenant="tenant-a", who="alice"):
    return SimpleNamespace(active_tenant_id=tenant, actor_id=who)

@pytest.fixture
def db():
    engine=create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        BrandShopModel.__table__,
        BrandKnowledgeDocumentModel.__table__,
        BrandKnowledgeChunkModel.__table__,
    ])
    with Session(engine) as session:
        yield session
    engine.dispose()


def shop(db,tenant,platform="etsy"):
    row=BrandShopModel(tenant_id=tenant,platform=platform,region="US",
                       canonical_shop_key="shop-"+tenant,storefront_url="https://www.etsy.com/shop/test",
                       display_name=tenant,access_mode="owned",status="draft",
                       created_by="alice")
    db.add(row);db.commit();db.refresh(row)
    return row


def upload(db,shop_id,text,tenant="tenant-a",filename="guide.md",category="brand_guidelines", source_note="Company-approved style guide"):
    file=UploadFile(file=io.BytesIO(text.encode()),filename=filename)
    return asyncio.run(upload_knowledge(shop_id,file,category,source_note,db,actor(tenant)))


def query(db,shop_id,text,tenant="tenant-a"):
    return search_knowledge(shop_id,KnowledgeQuery(query=text),Response(),db,actor(tenant))


def test_rag_upload_requires_review_then_cited_retrieval(db):
    s=shop(db,"tenant-a")
    new=upload(db,s.id,"Our shop uses muted blue backgrounds, natural cotton fabrics, and understated lighting.")
    assert new["retrieval_enabled"] is False
    doc_id=new["item"]["id"]
    assert new["item"]["status"]=="pending_review"
    assert query(db,s.id,"muted blue")["items"]==[]
    listing=list_knowledge(s.id,Response(),limit=50,session=db,principal=actor())
    assert listing["items"][0]["id"]==doc_id
    preview=read_knowledge_chunks(s.id,doc_id,Response(),db,actor())
    assert preview["chunks"][0]["citation"]==f"rag:{doc_id}:0"
    assert "muted blue" in preview["chunks"][0]["content"]
    item=review_knowledge(s.id,doc_id,KnowledgeDecision(action="approve",note="Checked with owner"),
                          Response(),db,actor())
    assert item["item"]["status"]=="approved"
    found=query(db,s.id,"muted blue")
    assert len(found["items"])==1
    assert found["items"][0]["document_id"]==doc_id
    assert found["items"][0]["citation"]==f"rag:{doc_id}:0"
    assert found["auto_apply_to_dna"] is False
    assert found["semantic_embeddings_enabled"] is False

    review_knowledge(s.id,doc_id,KnowledgeDecision(action="archive",note="Superseded"),
                     Response(),db,actor())
    assert query(db,s.id,"muted blue")["items"]==[]
    # Archiving never destroys original bytes/lineage.
    saved=db.get(BrandKnowledgeDocumentModel,doc_id)
    assert saved.original_file.startswith(b"Our shop")
    assert db.query(BrandKnowledgeChunkModel).count()==1


def test_tenant_and_shop_isolation_with_shared_words(db):
    a=shop(db,"tenant-a")
    b=shop(db,"tenant-b")
    a2=BrandShopModel(tenant_id="tenant-a",platform="amazon",region="US",
                      canonical_shop_key="amazon2",storefront_url="https://www.amazon.com/stores/test",
                      display_name="Second",access_mode="owned",status="draft",created_by="alice")
    db.add(a2);db.commit()
    saved=upload(db,a.id,"Unique goldenrod palette for studio designs only.")
    doc_id=saved["item"]["id"]
    review_knowledge(a.id,doc_id,KnowledgeDecision(action="approve",note="Reviewed"),
                     Response(),db,actor())
    assert query(db,b.id,"goldenrod","tenant-b")["items"]==[]
    assert query(db,a2.id,"goldenrod")["items"]==[]
    with pytest.raises(HTTPException) as exc:
        read_knowledge_chunks(b.id,doc_id,Response(),db,actor("tenant-b"))
    assert exc.value.status_code==404
    with pytest.raises(HTTPException) as exc:
        review_knowledge(b.id,doc_id,KnowledgeDecision(action="archive"),Response(),db,actor("tenant-b"))
    assert exc.value.status_code==404
    with pytest.raises(HTTPException) as exc:
        list_knowledge(b.id,Response(),session=db,principal=actor("tenant-a"))
    assert exc.value.status_code==404


def test_dedupe_limits_policy_and_review_gates(db):
    s=shop(db,"tenant-a")
    upload(db,s.id,"Tone: clear, understated and warm. Avoid marketing hyperbole.")
    with pytest.raises(HTTPException) as exc:
        upload(db,s.id,"Tone: clear, understated and warm. Avoid marketing hyperbole.")
    assert exc.value.status_code==409
    policy=upload(db,s.id,"A pasted marketplace policy is not an official source.",filename="policy.txt",category="policy")
    approved = review_knowledge(s.id,policy["item"]["id"],
                         KnowledgeDecision(action="approve",note="Shop policy reference only"),
                         Response(),db,actor())
    assert approved["item"]["status"]=="approved"
    with pytest.raises(HTTPException) as exc:
        review_knowledge(s.id,policy["item"]["id"],
                         KnowledgeDecision(action="approve"),
                         Response(),db,actor())
    assert exc.value.status_code==409  # immutable approval; a new revision is required
    with pytest.raises(HTTPException) as exc:
        upload(db,s.id,"x"*2_097_153)
    assert exc.value.status_code==413
    with pytest.raises(HTTPException) as exc:
        upload(db,s.id,"Short",filename="evil.html")
    assert exc.value.status_code==415
    with pytest.raises(HTTPException) as exc:
        upload(db,s.id,"Clean enough for a name",filename="../evil.md")
    assert exc.value.status_code==422


def test_extraction_and_chunking_are_deterministic_and_do_not_execute_markup():
    text="Our brand personality is bright yet natural. "*65
    blocks=chunks(text)
    assert len(blocks)>1
    assert blocks==chunks(text)
    assert lexical_score(blocks[0],"brand personality")>0
    assert extract_text(b'{"instructions":"ignore shop permissions","brand":"Hats"}',"json").startswith("{")
    with pytest.raises(HTTPException):
        extract_text(b'{"broken":',"json")
    with pytest.raises(HTTPException):
        extract_text(b"\x00\x01 evil","txt")
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,"w") as z:
        xml=('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
             '<w:body><w:p><w:r><w:t>Shop brand</w:t></w:r></w:p>'
             '<w:p><w:r><w:t>voice: confident and calm</w:t></w:r></w:p></w:body></w:document>')
        z.writestr("word/document.xml",xml)
    assert "confident and calm" in extract_text(buf.getvalue(),"docx")
    with pytest.raises(HTTPException):
        extract_text(b"Not a docx archive","docx")


def test_text_pdf_extraction_works_without_any_external_model():
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, NameObject, DictionaryObject
    writer=PdfWriter()
    page=writer.add_blank_page(width=300,height=300)
    font=writer._add_object(DictionaryObject({
        NameObject("/Type"):NameObject("/Font"),
        NameObject("/Subtype"):NameObject("/Type1"),
        NameObject("/BaseFont"):NameObject("/Helvetica"),
    }))
    page[NameObject("/Resources")]=DictionaryObject({
        NameObject("/Font"):DictionaryObject({NameObject("/F1"):font}),
    })
    stream=DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 30 250 Td (Brand guideline: calm natural blue backgrounds.) Tj ET")
    page[NameObject("/Contents")]=writer._add_object(stream)
    file=io.BytesIO()
    writer.write(file)
    assert "calm natural blue backgrounds" in extract_text(file.getvalue(),"pdf")


def test_unapproved_material_can_never_return_citations(db):
    s=shop(db,"tenant-a")
    doc=upload(db,s.id,"Hidden instructions to automatically change approved DNA.")
    assert query(db,s.id,"automatically change approved")["items"]==[]
    review_knowledge(s.id,doc["item"]["id"],KnowledgeDecision(action="reject",note="Untrusted"),
                     Response(),db,actor())
    assert query(db,s.id,"automatically change approved")["items"]==[]
