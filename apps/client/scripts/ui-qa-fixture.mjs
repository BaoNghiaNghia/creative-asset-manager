import fs from "node:fs/promises";
import path from "node:path";

function json(body, status = 200) {
  return {
    status,
    contentType: "application/json; charset=utf-8",
    body: JSON.stringify(body),
  };
}

function fixtureApiResponse(fixture, method, pathname) {
  const routes = Array.isArray(fixture.apiRoutes) ? fixture.apiRoutes : [];
  for (const route of routes) {
    if (!route || typeof route !== "object") continue;
    const routeMethod = String(route.method || "GET").toUpperCase();
    if (routeMethod !== method) continue;
    const exact = typeof route.path === "string" && route.path === pathname;
    const prefix = typeof route.prefix === "string" && pathname.startsWith(route.prefix);
    if (!exact && !prefix) continue;
    const status = Number.isInteger(route.status) ? route.status : 200;
    return json(route.body ?? {}, status);
  }
  return null;
}

function folderPayload(fixture, parentId) {
  const folder = fixture.folders?.[parentId];
  if (!folder) {
    return json({ detail: { message: `Unknown fixture folder: ${parentId}` } }, 404);
  }
  return json({
    ...folder,
    next_page_token: folder.next_page_token ?? null,
    has_more: Boolean(folder.has_more),
  });
}

function filterSearchItems(fixture, query) {
  const normalized = String(query || "").trim().toLocaleLowerCase();
  const items = Array.isArray(fixture.search?.items) ? fixture.search.items : [];
  if (!normalized) return items;
  const tokens = normalized.split(/\s+/).filter(Boolean);
  return items.filter((item) => {
    const haystack = [
      item.name,
      item.folder_path,
      ...(Array.isArray(item.ancestor_names) ? item.ancestor_names : []),
    ].filter(Boolean).join(" ").toLocaleLowerCase();
    return tokens.every((token) => haystack.includes(token));
  });
}

function defaultCapabilities() {
  return {
    selected_version: "v3",
    readiness: "ready",
    search_available: true,
    viewer_scoped: false,
    failure_code: null,
    facet_names: [],
    examples: ["embroidery", "ugc review"],
  };
}

function buildSearchResponse(fixture, body) {
  const items = filterSearchItems(fixture, body?.query || body?.q || "");
  return {
    items,
    total: items.length,
    facets: {},
    parsed_query: {
      mode: "fixture",
      clauses: body?.query || body?.q
        ? [{ kind: "text", value: String(body.query || body.q) }]
        : [],
    },
    next_cursor: null,
    has_more: false,
  };
}

function metadataResponse(fixture, body) {
  const metadata = fixture.metadata || {};
  const itemIds = Array.isArray(body?.item_ids) ? body.item_ids : [];
  return {
    items: itemIds.map((itemId) => metadata[itemId] || {
      item_id: itemId,
      tag_ids: [],
      rating: null,
      processing_status: "indexed",
    }),
  };
}

export async function loadUiQaFixture(fixturePath) {
  if (!fixturePath) return null;
  const absolutePath = path.resolve(fixturePath);
  const raw = await fs.readFile(absolutePath, "utf8");
  const fixture = JSON.parse(raw);
  if (!fixture || typeof fixture !== "object") {
    throw new Error("UI QA fixture must be a JSON object.");
  }
  for (const required of ["identity", "providerSession", "sources", "folders"]) {
    if (!(required in fixture)) throw new Error(`UI QA fixture is missing "${required}".`);
  }
  if (fixture.apiRoutes !== undefined) {
    if (!Array.isArray(fixture.apiRoutes)) {
      throw new Error("UI QA fixture apiRoutes must be an array.");
    }
    for (const [index, route] of fixture.apiRoutes.entries()) {
      if (!route || typeof route !== "object") {
        throw new Error(`UI QA fixture apiRoutes[${index}] must be an object.`);
      }
      if (typeof route.path !== "string" && typeof route.prefix !== "string") {
        throw new Error(
          `UI QA fixture apiRoutes[${index}] must define an exact path or prefix.`,
        );
      }
      for (const value of [route.path, route.prefix]) {
        if (value !== undefined && (!value.startsWith("/") || value.startsWith("//"))) {
          throw new Error(
            `UI QA fixture apiRoutes[${index}] must use an origin-relative API path.`,
          );
        }
      }
    }
  }
  return { ...fixture, absolutePath };
}

export async function installUiQaFixture(context, fixture, baseUrl) {
  if (!fixture) return;
  const origin = new URL(baseUrl).origin;
  await context.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== origin) {
      await route.abort("blockedbyclient");
      return;
    }

    const method = request.method().toUpperCase();
    const pathname = url.pathname;
    const customResponse = fixtureApiResponse(fixture, method, pathname);
    let response;

    if (method === "GET" && pathname === "/api/v1/auth/identity") {
      response = json(fixture.identity);
    } else if (method === "GET" && pathname === "/api/auth/google/session") {
      response = json(fixture.providerSession);
    } else if (method === "GET" && pathname === "/api/auth/microsoft/session") {
      response = json({ authenticated: false, user: null }, 401);
    } else if (method === "GET" && pathname === "/api/sources") {
      response = json(fixture.sources);
    } else if (customResponse) {
      response = customResponse;
    } else if (method === "GET" && pathname === "/api/explorer/viewer/bootstrap") {
      response = json(fixture.viewerBootstrap || {
        sources: [],
        auto_selected_source_id: null,
        auto_selected_folder_id: null,
      });
    } else if (method === "GET" && pathname === "/api/explorer/children") {
      response = folderPayload(fixture, url.searchParams.get("parent_id") || "root");
    } else if (method === "GET" && pathname === "/api/explorer/folders") {
      const parentId = url.searchParams.get("parent_id") || "root";
      const folder = fixture.folders?.[parentId];
      response = folder
        ? json((folder.children || []).filter((item) => item.kind === "folder"))
        : json({ detail: { message: `Unknown fixture folder: ${parentId}` } }, 404);
    } else if (method === "GET" && /^\/api\/explorer\/folders\/[^/]+\/note$/.test(pathname)) {
      const folderId = decodeURIComponent(pathname.split("/").at(-2) || "");
      response = json({
        requested_folder_id: folderId,
        note_owner_folder_id: null,
        note_owner_folder_name: null,
        is_inherited: false,
        content_markdown: "",
        updated_at: null,
        updated_by: null,
      });
    } else if (method === "GET" && pathname === "/api/v1/search/capabilities") {
      response = json(fixture.search?.capabilities || defaultCapabilities());
    } else if (method === "GET" && pathname === "/api/v1/search/suggestions") {
      const q = (url.searchParams.get("q") || "").trim().toLocaleLowerCase();
      const suggestions = Array.isArray(fixture.search?.suggestions)
        ? fixture.search.suggestions.filter((item) => !q || item.text.toLocaleLowerCase().includes(q))
        : [];
      response = json({ suggestions });
    } else if (method === "POST" && pathname === "/api/v1/search") {
      const body = request.postDataJSON?.() || {};
      response = json(buildSearchResponse(fixture, body));
    } else if (method === "GET" && pathname === "/api/v1/search/folders") {
      response = json({ items: [], total: 0 });
    } else if (method === "POST" && pathname === "/api/v1/search/video") {
      response = json({ items: [], total: 0, took_ms: 1 });
    } else if (method === "GET" && pathname.startsWith("/api/v1/search/video/")) {
      response = json({ item: null }, 404);
    } else if (method === "POST" && pathname === "/api/metadata/query") {
      const body = request.postDataJSON?.() || {};
      response = json(metadataResponse(fixture, body));
    } else {
      response = json({
        detail: {
          message: `Unhandled UI QA fixture route: ${method} ${pathname}`,
        },
      }, 599);
    }

    await route.fulfill(response);
  });
}
