import { describe, expect, it, vi } from "vitest";

import { LocationBreadcrumb, type LocationBreadcrumbNode } from "./LocationBreadcrumb";

describe("Location breadcrumb navigation", () => {
  it("passes every preceding segment as ancestors when a breadcrumb part is opened", () => {
    const nodes: LocationBreadcrumbNode[] = [
      { id: "root", name: "My Drive" },
      { id: "amazon", name: "Amazon" },
      { id: "style", name: "Style 1" },
      { id: "macro", name: "UGC + Macro Video" },
    ];
    const onOpenFolder = vi.fn();
    const tree = LocationBreadcrumb({ nodes, onOpenFolder }) as any;
    const styleButton = tree.props.children[2].props.children[1];

    styleButton.props.onClick();

    expect(onOpenFolder).toHaveBeenCalledWith("style", nodes.slice(0, 2));
  });

  it("opens the root segment without inventing an ancestor", () => {
    const nodes = [{ id: "root", name: "My Drive" }];
    const onOpenFolder = vi.fn();
    const tree = LocationBreadcrumb({ nodes, onOpenFolder }) as any;

    tree.props.children[0].props.children[1].props.onClick();

    expect(onOpenFolder).toHaveBeenCalledWith("root", []);
  });
});
