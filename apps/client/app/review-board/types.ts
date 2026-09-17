import type { EditorJson } from "../public-review/RichAnnotation";
export type BoardStatus = "open" | "resolved" | "all";
export type BoardSort = "newest" | "oldest" | "recently_updated" | "recently_resolved";
export type BoardIssue = { id:string; status:"open"|"resolved"; annotation_preview:string; created_at:string; updated_at:string; anchor_x:number|null; anchor_y:number|null; reviewer:{display_name:string}; share:{id:string;name:string}; asset:{asset_id:string;source_asset_id:string;filename:string|null;media_type:string|null}; reply_count:number; resolved_at:string|null; resolver:{actor_id:string}|null };
export type BoardIssueDetail = BoardIssue & { content_json:EditorJson; plain_text:string; replies:Array<{id:string;content_json:EditorJson;plain_text:string;created_at:string;updated_at:string;reviewer:{display_name:string}}> };
export type BoardStats = {total_issues:number;open_issues:number;resolved_issues:number;resolution_rate:number;assets_with_open_issues:number;shares_with_open_issues:number};
export type BoardTransition = {id:string;status:"open"|"resolved";resolved_at:string|null;resolver:{actor_id:string}|null;updated_at:string;transitioned:boolean};
export type BoardFilters = {status:BoardStatus;reviewer:string;pinned:""|"true"|"false";created_from:string;created_to:string;sort:BoardSort;page:number;page_size:25|50|100;share_id?:string;external_source_id?:string;folder_external_id?:string;asset_id?:string};
export type BoardPage = {items:BoardIssue[];page:number;page_size:number;total:number};
