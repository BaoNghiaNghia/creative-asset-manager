import { describe, expect, it } from "vitest";
import { createNativeDragTicketStore } from "./nativeDragTickets";

describe("native drag ticket store", () => {
  it("issues short-lived one-shot tickets bound to the renderer sender", () => {
    let now = 1000;
    const store = createNativeDragTicketStore<string>(5000, () => now);
    const issued = store.issue({ senderId: 7, files: ["C:/tmp/photo.jpg"], icon: "icon" });

    expect(issued.expiresAt).toBe(6000);
    expect(store.take(issued.ticket, 8)).toBeUndefined();

    const payload = store.take(issued.ticket, 7);
    expect(payload?.files).toEqual(["C:/tmp/photo.jpg"]);
    expect(payload?.icon).toBe("icon");
    expect(store.take(issued.ticket, 7)).toBeUndefined();

    const expired = store.issue({ senderId: 7, files: ["C:/tmp/old.jpg"], icon: "icon" });
    now = expired.expiresAt;
    expect(store.take(expired.ticket, 7)).toBeUndefined();
    expect(store.size()).toBe(0);
  });
});
