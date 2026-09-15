from __future__ import annotations
import hashlib, json, unicodedata
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

class KnowledgePackError(ValueError): pass
class KnowledgeStage(str, Enum):
    IDEA_STORY = "idea_story"
    SEEDANCE_2_5 = "seedance_2_5"
    GOOGLE_OMNI = "google_omni"
SUPPORTED_PLATFORMS = frozenset({"etsy","amazon"})
SUPPORTED_STAGES = frozenset(stage.value for stage in KnowledgeStage)

@dataclass(frozen=True, slots=True)
class KnowledgeFile:
    path: str
    sha256: str
    content: str
    def as_dict(self): return {"path": self.path, "sha256": self.sha256, "content": self.content}

@dataclass(frozen=True, slots=True)
class KnowledgeBundle:
    platform: str
    stage: KnowledgeStage
    knowledge_snapshot_id: str
    files: tuple[KnowledgeFile, ...]
    combined_text: str

@dataclass(frozen=True, slots=True)
class KnowledgeSnapshot:
    schema_version: int
    pack_version: str
    platform: str
    selection: dict[str, tuple[str, ...]]
    files: tuple[KnowledgeFile, ...]
    snapshot_id: str
    def as_payload(self):
        return {"schema_version": self.schema_version, "pack_version": self.pack_version, "platform": self.platform,
                "selection": {k:list(v) for k,v in sorted(self.selection.items())},
                "files": [f.as_dict() for f in self.files]}
    def as_json_bytes(self):
        return json.dumps(self.as_payload(), ensure_ascii=False, sort_keys=True, separators=(",",":")).encode()

def normalize_text(raw: bytes) -> str:
    try: text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc: raise KnowledgePackError("knowledge file is not valid UTF-8") from exc
    text = unicodedata.normalize("NFC", text.replace("\r\n","\n").replace("\r","\n"))
    return text.rstrip("\n") + "\n"

def _sha(content: str): return hashlib.sha256(content.encode("utf-8")).hexdigest()

class KnowledgeLoader:
    def __init__(self, root: str|Path|None=None):
        self.root = (Path(root) if root is not None else Path(__file__).with_name("knowledge")).resolve()
    def normalize_path(self, logical_path: str) -> Path:
        if not isinstance(logical_path,str) or not logical_path or "\x00" in logical_path: raise KnowledgePackError("invalid knowledge path")
        value=logical_path.replace("\\","/")
        p=Path(value)
        if p.is_absolute() or value.startswith("/") or (len(value)>1 and value[1]==":") or any(x in {"",".",".."} for x in value.split("/")):
            raise KnowledgePackError("absolute or traversal knowledge path is forbidden")
        result=(self.root / Path(*value.split("/"))).resolve()
        try: result.relative_to(self.root)
        except ValueError as exc: raise KnowledgePackError("knowledge path escapes canonical root") from exc
        return result
    def load_manifest(self):
        try: manifest=json.loads(self.normalize_path("manifest.json").read_text(encoding="utf-8-sig"))
        except (OSError,UnicodeDecodeError,json.JSONDecodeError) as exc: raise KnowledgePackError("knowledge manifest is unavailable or invalid") from exc
        self.validate_manifest(manifest); return manifest
    def validate_manifest(self, manifest: Mapping[str,Any]):
        if not isinstance(manifest,Mapping) or manifest.get("schema_version")!=1 or not isinstance(manifest.get("pack_version"),str): raise KnowledgePackError("unsupported knowledge manifest schema")
        stages=manifest.get("stages")
        if not isinstance(stages,Mapping) or set(stages)!=SUPPORTED_STAGES: raise KnowledgePackError("manifest must define exactly the supported stages")
        seen=set()
        for stage in sorted(SUPPORTED_STAGES):
            entries=stages[stage]
            if not isinstance(entries,list) or not entries: raise KnowledgePackError(f"stage {stage} has no files")
            stage_seen=set()
            for entry in entries:
                if not isinstance(entry,str) or not entry or entry.count("{platform}")>1 or ("{" in entry and entry!="{platform}_rules.md"): raise KnowledgePackError("unsupported platform placeholder")
                if entry in stage_seen: raise KnowledgePackError("duplicate manifest path")
                stage_seen.add(entry)
                seen.add(entry)
                path=entry.replace("{platform}","etsy"); self.normalize_path(path)
                if not self.normalize_path(path).is_file(): raise KnowledgePackError(f"knowledge file not found: {entry}")
        examples=manifest.get("examples", [])
        if not isinstance(examples, (list, Mapping)): raise KnowledgePackError("examples must be a list or stage map")
        example_values = list(examples) if isinstance(examples, list) else [item for values in examples.values() for item in values]
        if isinstance(examples, Mapping) and any(key not in SUPPORTED_STAGES for key in examples):
            raise KnowledgePackError("unknown example stage")
        for entry in example_values:
            if not isinstance(entry,str) or entry in seen: raise KnowledgePackError("invalid or duplicate example path")
            seen.add(entry)
            self.normalize_path(entry)
            if not self.normalize_path(entry).is_file(): raise KnowledgePackError(f"example file not found: {entry}")
    def select_files(self, platform, stage):
        platform = platform.value if hasattr(platform,"value") else str(platform)
        stage = stage.value if hasattr(stage,"value") else str(stage)
        if platform not in SUPPORTED_PLATFORMS or stage not in SUPPORTED_STAGES: raise KnowledgePackError("unsupported platform or stage")
        manifest = self.load_manifest()
        values = [x.replace("{platform}", platform) for x in manifest["stages"][stage]]
        examples = manifest.get("examples", [])
        if isinstance(examples, list) and stage == KnowledgeStage.IDEA_STORY.value:
            values.extend(examples)
        elif isinstance(examples, Mapping):
            values.extend(examples.get(stage, []))
        values = tuple(values)
        if len(values)!=len(set(values)): raise KnowledgePackError("duplicate selected knowledge path")
        return values
    def _read(self,path):
        p=self.normalize_path(path)
        if not p.is_file(): raise KnowledgePackError(f"knowledge file not found: {path}")
        content=normalize_text(p.read_bytes()); return KnowledgeFile(path.replace("\\","/"),_sha(content),content)
    def create_run_snapshot(self, platform):
        platform=platform.value if hasattr(platform,"value") else str(platform)
        if platform not in SUPPORTED_PLATFORMS: raise KnowledgePackError("unsupported platform")
        manifest=self.load_manifest()
        selection={stage:tuple(sorted(set(self.select_files(platform, stage)))) for stage in sorted(SUPPORTED_STAGES)}
        files=tuple(self._read(p) for p in sorted({p for paths in selection.values() for p in paths}))
        payload={"schema_version":1,"pack_version":manifest["pack_version"],"platform":platform,"selection":{k:list(v) for k,v in sorted(selection.items())},"files":[f.as_dict() for f in files]}
        sid="sha256:"+hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()).hexdigest()
        return KnowledgeSnapshot(1,manifest["pack_version"],platform,selection,files,sid)
    def load_bundle(self, platform, stage, snapshot=None):
        if snapshot is None:
            selected=self.select_files(platform,stage); files=tuple(self._read(p) for p in selected); sid=self.create_run_snapshot(platform).snapshot_id
        else:
            verified=self.verify_snapshot(snapshot); selected=verified.selection[stage.value if hasattr(stage,"value") else str(stage)]; by={f.path:f for f in verified.files}; files=tuple(by[p] for p in selected); sid=verified.snapshot_id
        stage_value=stage.value if hasattr(stage,"value") else str(stage); platform_value=platform.value if hasattr(platform,"value") else str(platform)
        combined="\n".join(f"--- BEGIN {f.path} ---\n{f.content}--- END {f.path} ---" for f in files)+"\n"
        return KnowledgeBundle(platform_value,KnowledgeStage(stage_value),sid,files,combined)
    def verify_snapshot(self, snapshot):
        if isinstance(snapshot,Mapping):
            try: parsed=KnowledgeSnapshot(int(snapshot["schema_version"]),str(snapshot["pack_version"]),str(snapshot["platform"]),{str(k):tuple(str(x) for x in v) for k,v in snapshot["selection"].items()},tuple(KnowledgeFile(str(x["path"]),str(x["sha256"]),str(x["content"])) for x in snapshot["files"]),"")
            except (KeyError,TypeError,ValueError) as exc: raise KnowledgePackError("malformed knowledge snapshot") from exc
        else: parsed=snapshot
        if parsed.schema_version!=1 or parsed.platform not in SUPPORTED_PLATFORMS or set(parsed.selection)!=SUPPORTED_STAGES: raise KnowledgePackError("invalid knowledge snapshot")
        seen=set()
        for f in parsed.files:
            self.normalize_path(f.path)
            if f.path in seen or _sha(normalize_text(f.content.encode("utf-8")))!=f.sha256: raise KnowledgePackError("knowledge file hash mismatch")
            seen.add(f.path)
        selected_paths = {path for values in parsed.selection.values() for path in values}
        if not selected_paths.issubset(seen):
            raise KnowledgePackError("snapshot selection references missing file")
        payload={"schema_version":parsed.schema_version,"pack_version":parsed.pack_version,"platform":parsed.platform,"selection":{k:list(v) for k,v in sorted(parsed.selection.items())},"files":[f.as_dict() for f in sorted(parsed.files,key=lambda x:x.path)]}
        sid="sha256:"+hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()).hexdigest()
        if parsed.snapshot_id and parsed.snapshot_id!=sid: raise KnowledgePackError("knowledge snapshot id mismatch")
        return KnowledgeSnapshot(parsed.schema_version,parsed.pack_version,parsed.platform,parsed.selection,tuple(sorted(parsed.files,key=lambda x:x.path)),sid)
    def bundle_for_stage_from_snapshot(self,snapshot,stage):
        verified=self.verify_snapshot(snapshot); return self.load_bundle(verified.platform,stage,verified)

    async def load_run_snapshot(self, tenant_id: str, pipeline_run_id: str, session, gateway) -> KnowledgeSnapshot:
        from sqlalchemy import select
        from app.modules.creative_pipeline.model import ArtifactModel, PipelineRunModel
        run = session.scalar(select(PipelineRunModel).where(
            PipelineRunModel.tenant_id == tenant_id, PipelineRunModel.id == pipeline_run_id
        ))
        if run is None or not run.knowledge_snapshot_id:
            raise KnowledgePackError("knowledge snapshot unavailable")
        artifact = session.scalar(select(ArtifactModel).where(
            ArtifactModel.tenant_id == tenant_id,
            ArtifactModel.pipeline_run_id == pipeline_run_id,
            ArtifactModel.artifact_type == "knowledge_snapshot",
            ArtifactModel.status == "available",
        ))
        if artifact is None or not artifact.external_file_id or not artifact.content_hash:
            raise KnowledgePackError("knowledge snapshot artifact inconsistent")
        downloader = getattr(gateway, "download_bytes", None)
        if downloader is None:
            raise KnowledgePackError("knowledge snapshot download unavailable")
        raw = downloader(artifact.external_file_id)
        if hasattr(raw, "__await__"):
            raw = await raw
        if not isinstance(raw, bytes) or hashlib.sha256(raw).hexdigest() != artifact.content_hash:
            raise KnowledgePackError("knowledge snapshot artifact mismatch")
        try:
            snapshot = self.verify_snapshot(json.loads(raw.decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise KnowledgePackError("knowledge snapshot artifact is not valid JSON") from exc
        if snapshot.snapshot_id != run.knowledge_snapshot_id:
            raise KnowledgePackError("knowledge snapshot id mismatch")
        return snapshot
