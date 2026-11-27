# CAM_IMAGEGEN_LINK_VIEWER_V1

Approved destination only: CAM ImageGen Designs, Drive folder
1JdCzemUBcLhfro5IbOq5jPze7rdDIJMl. The operator has explicitly requested
Anyone with the link / Viewer on this whole folder, including descendants.

## Policy
- Accept Restricted or non-discoverable anyone/reader on this exact configured
  root. The already-proven Muse-02 canary child may inherit the same ACL.
- Public editor/commenter, searchable permissions, domain-wide grants,
  unknown permission metadata, wrong owner and missing write capability remain
  blocked. All other roots retain their former policy.
- The owner OAuth check, tenant, asset ID, SHA, parent and storage validation
  remain required. Named collaborators are never changed.
- OAuth reconnect preserves a valid link-viewer grant. Normal workers do not
  create broad permissions.
- With inherited root Viewer, CAM sends canonical Drive /view links to SMS.
  Use targets[].video_urls for the TikTok Target Videos slot (one 9:16 video),
  with idea Fields as supplementary references. Verify target videos=1 by
  SMS readback. CAM does not upload MP4 bytes; SMS may fetch the link itself.
  Individual video grants/revokes are not performed.

## Explicit production action
After deploying the policy and checking all service health, an authorized root
operator can run:

```sh
bash current/scripts/cam-imagegen-root-sharing.sh --check
bash current/scripts/cam-imagegen-root-sharing.sh --apply --confirm-root 1JdCzemUBcLhfro5IbOq5jPze7rdDIJMl
bash current/scripts/cam-imagegen-root-sharing.sh --check
```

The existing redacted production environment wrapper passes the existing managed
OAuth credential to the app without printing it. The operation checks exact
root, owner identity, ownership, folder type, write and share capability and
existing ACL; it creates at most one non-discoverable reader grant. It never
changes other permissions. Stop and review on any ambiguous error; do not blindly
retry. Verify anonymous access separately in a logged-out browser. A HEAD
response alone does not establish playable video.

## Exposure and rollback
Anyone with the folder link may browse inherited content, including Native
Masters, listing images and MP4s. Descendants with limited access may differ.
This is not public editing; viewers may still download files when Drive allows.
No content copies or database changes. To reverse, the authorized owner must
set the root General access to Restricted in Drive; removing a root grant
does not remove pre-existing direct file grants. Restore Restricted before
rolling back to a release that requires it.
