import { randomUUID } from "node:crypto";

export type NativeDragTicketPayload<TIcon> = {
  senderId: number;
  files: string[];
  icon: TIcon;
  expiresAt: number;
};

export function createNativeDragTicketStore<TIcon>(
  ttlMs = 5000,
  now: () => number = Date.now,
) {
  const tickets = new Map<string, NativeDragTicketPayload<TIcon>>();

  function prune() {
    const current = now();
    for (const [ticket, payload] of tickets) {
      if (payload.expiresAt <= current) tickets.delete(ticket);
    }
  }

  return {
    issue(payload: Omit<NativeDragTicketPayload<TIcon>, "expiresAt">) {
      prune();
      const ticket = randomUUID();
      const expiresAt = now() + ttlMs;
      tickets.set(ticket, { ...payload, expiresAt });
      return { ticket, expiresAt };
    },
    take(ticket: string, senderId: number): NativeDragTicketPayload<TIcon> | undefined {
      prune();
      const payload = tickets.get(ticket);
      if (!payload || payload.senderId !== senderId) return undefined;
      tickets.delete(ticket);
      return payload;
    },
    size() {
      prune();
      return tickets.size;
    },
  };
}
