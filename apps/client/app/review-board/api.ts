import type { BoardFilters, BoardIssueDetail, BoardPage, BoardStats, BoardTransition } from "./types";
export class BoardApiError extends Error { constructor(message:string, readonly status:number) { super(message); } }
const base = "/api/v1/public-review/board";
async function request<T>(path:string, init:RequestInit={}):Promise<T>{ const response=await fetch(base+path,{credentials:"same-origin",...init,headers:{Accept:"application/json",...(init.body?{"Content-Type":"application/json"}:{}),...init.headers}}); if(!response.ok){const body=await response.json().catch(()=>({})) as {detail?:{message?:string}|string}; const detail=body.detail; throw new BoardApiError(typeof detail==="string"?detail:detail?.message||"Review Board is unavailable.",response.status);} return response.json() as Promise<T>; }
export function boardQuery(filters:BoardFilters){const params=new URLSearchParams({status:filters.status,sort:filters.sort,page:String(filters.page),page_size:String(filters.page_size)}); for(const key of ["reviewer","pinned","created_from","created_to","share_id","external_source_id","folder_external_id","asset_id"] as const){const value=filters[key];if(value)params.set(key,value);} return params.toString();}
export const fetchBoardIssues=(filters:BoardFilters,signal?:AbortSignal)=>request<BoardPage>("/issues?"+boardQuery(filters),{signal});
export const fetchBoardIssue=(id:string,signal?:AbortSignal)=>request<BoardIssueDetail>("/issues/"+encodeURIComponent(id),{signal});
export const fetchBoardStats=(signal?:AbortSignal)=>request<BoardStats>("/stats",{signal});
export const resolveBoardIssue=(id:string)=>request<BoardTransition>("/issues/"+encodeURIComponent(id)+"/resolve",{method:"POST"});
export const reopenBoardIssue=(id:string)=>request<BoardTransition>("/issues/"+encodeURIComponent(id)+"/reopen",{method:"POST"});
