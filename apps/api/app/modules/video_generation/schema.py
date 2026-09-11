from datetime import datetime
from typing import Literal
from pydantic import BaseModel,Field
class VideoGenerationRequest(BaseModel): client_request_id:str=Field(min_length=1,max_length=200);prompt:str=Field(min_length=1,max_length=4000);model:Literal["seedance-2.0","seedance-2.5"];aspect_ratio:Literal["16:9","9:16","1:1","4:3","3:4"];duration_seconds:Literal[10,15,30];reference_asset_ids:list[str]=Field(default_factory=list,max_length=8)
class VideoGenerationResponse(BaseModel): id:str;status:str;provider:str;model:str;prompt:str;aspect_ratio:str;duration_seconds:int;reference_asset_ids:list[str];output_asset_id:str|None;error:dict|None;created_at:datetime;submitted_at:datetime|None;completed_at:datetime|None
class Capability(BaseModel): enabled:bool;provider:str="dola";models:list[str];aspect_ratios:list[str];durations:list[int];max_references:int;allowed_reference_mime_types:list[str];max_reference_bytes:int;max_reference_total_bytes:int
