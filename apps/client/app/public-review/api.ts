import type { EditorJson } from "./RichAnnotation";
export type Bootstrap={public_id:string;name:string;allow_comments:boolean;allow_download:boolean;expires_at:string|null};
export type Folder={source_id:string;folder_id:string;name:string};
export type FolderResolution={folder:Folder;trail:Folder[]};
export type Asset={kind:"asset";asset_id:string;source_asset_id:string;filename:string;media_type:string|null;thumbnail_url:string;preview_url:string;annotation_count?:number};
export type Child=(Folder&{kind:"folder"})|Asset;
export type ChildPage={items:Child[];next_offset:number|null};
export type Annotation={id:string;author:{display_name:string};content_json:EditorJson;plain_text:string;parent_annotation_id:string|null;anchor_x:number|null;anchor_y:number|null;created_at:string;updated_at:string;can_edit:boolean;can_delete:boolean};
export type PlaybackTicket={url:string;cdn:boolean;expires_at:number|null};
const base=(id:string)=>"/api/public/review/"+encodeURIComponent(id);
async function call<T>(url:string,init?:RequestInit):Promise<T>{const r=await fetch(url,{credentials:"same-origin",...init,headers:{"Content-Type":"application/json",...(init?.headers||{})}});if(!r.ok)throw new Error("unavailable");return r.json();}
export const api={
 exchange:(id:string,key:string)=>call(base(id)+"/session",{method:"POST",body:JSON.stringify({key})}), bootstrap:(id:string)=>call<Bootstrap>(base(id)+"/bootstrap"), folders:(id:string)=>call<{items:Folder[]}>(base(id)+"/folders"), folder:(id:string,folderId:string)=>call<FolderResolution>(base(id)+"/folders/"+encodeURIComponent(folderId)), children:(id:string,f:Folder,offset=0,limit=50)=>call<ChildPage>(base(id)+"/folders/"+encodeURIComponent(f.folder_id)+"/children?source_id="+encodeURIComponent(f.source_id)+"&offset="+offset+"&limit_value="+limit), search:(id:string,q:string)=>call<{items:Asset[]}>(base(id)+"/search?q="+encodeURIComponent(q)), annotations:(id:string,a:Asset)=>call<{items:Annotation[]}>(base(id)+"/assets/"+a.asset_id+"/annotations?source_asset_id="+a.source_asset_id),
 playbackTicket:(id:string,a:Asset)=>call<PlaybackTicket>(base(id)+"/assets/"+a.asset_id+"/playback-ticket?source_asset_id="+encodeURIComponent(a.source_asset_id)),
 prewarm:(id:string,a:Asset)=>call<{accepted:boolean}>(base(id)+"/assets/"+a.asset_id+"/prewarm?source_asset_id="+encodeURIComponent(a.source_asset_id),{method:"POST"}),
 create:(id:string,a:Asset,content_json:EditorJson,options?:{parentAnnotationId?:string;anchorX?:number;anchorY?:number})=>call<Annotation>(base(id)+"/assets/"+a.asset_id+"/annotations?source_asset_id="+a.source_asset_id,{method:"POST",body:JSON.stringify({content_json,...(options?.parentAnnotationId?{parent_annotation_id:options.parentAnnotationId}:{}),...(options?.anchorX!==undefined&&options?.anchorY!==undefined?{anchor_x:options.anchorX,anchor_y:options.anchorY}:{})})}),
 update:(id:string,n:string,content_json:EditorJson,anchors?:{anchorX?:number;anchorY?:number})=>call<Annotation>(base(id)+"/annotations/"+n,{method:"PATCH",body:JSON.stringify({content_json,...(anchors?.anchorX!==undefined&&anchors?.anchorY!==undefined?{anchor_x:anchors.anchorX,anchor_y:anchors.anchorY}:{})})}), remove:(id:string,n:string)=>call(base(id)+"/annotations/"+n,{method:"DELETE"})
};
