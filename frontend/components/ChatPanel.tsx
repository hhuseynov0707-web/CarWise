"use client";

import { useEffect, useRef, useState } from "react";
import { Callout } from "@/components/ui";
import { ApiError, askExpert } from "@/lib/api";
import { useLocale } from "@/lib/locale";
import type { ChatTurn } from "@/lib/types";

/**
 * The expert conversation.
 *
 * The AI label sits above the thread rather than on each reply: repeating it
 * on every message trains people to stop reading it, and it needs to be read
 * once.
 */
export function ChatPanel({ target }: { target?: string | null }) {
  // "listing-123" names an advert to assess; anything else is a configuration
  // to ground a conversation in.
  const listingId = target?.startsWith("listing-")
    ? Number(target.slice("listing-".length)) || null
    : null;
  const configId = listingId ? null : target ?? null;
  const { t, locale } = useLocale();
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns, busy]);

  // Arriving with an advert is already the question. Asking it back would make
  // somebody type out what they just clicked on.
  const opened = useRef<number | null>(null);
  useEffect(() => {
    if (!listingId || opened.current === listingId) return;
    opened.current = listingId;
    setTurns([]);
    void (async () => {
      setBusy(true);
      setError(null);
      try {
        const response = await askExpert({
          messages: [],
          listing_id: listingId,
          language: locale,
        });
        setTurns([{ role: "assistant", content: response.reply }]);
      } catch (caught) {
        setError(
          caught instanceof ApiError && caught.status === 503
            ? t.chat.unavailable
            : caught instanceof ApiError
              ? caught.message
              : String(caught),
        );
      } finally {
        setBusy(false);
      }
    })();
  }, [listingId, locale, t.chat.unavailable]);

  async function send(event: React.FormEvent) {
    event.preventDefault();
    const question = draft.trim();
    if (!question || busy) return;

    const next: ChatTurn[] = [...turns, { role: "user", content: question }];
    setTurns(next);
    setDraft("");
    setBusy(true);
    setError(null);

    try {
      const response = await askExpert({
        // Only the recent exchange is replayed. The whole history would grow
        // the request without end, and this is charged per token.
        messages: next.slice(-12),
        config_id: configId,
        listing_id: listingId,
        language: locale,
      });
      setTurns((current) => [...current, { role: "assistant", content: response.reply }]);
    } catch (caught) {
      setError(
        caught instanceof ApiError && caught.status === 503
          ? t.chat.unavailable
          : caught instanceof ApiError
            ? caught.message
            : String(caught),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <h1 className="text-figure-lg font-semibold tracking-tight text-ink">{t.nav.chat}</h1>
      <p className="mt-2 max-w-2xl text-sm leading-relaxed text-ink-soft">{t.chat.lead}</p>

      {target ? (
        <p className="mt-3 text-xs text-ink-muted">{t.chat.groundedIn}.</p>
      ) : null}

      <p className="mt-4 text-xs text-ink-faint">{t.chat.aiNote}</p>

      <div className="mt-4 grid gap-3">
        {turns.map((turn, index) => (
          <div
            key={index}
            className={
              turn.role === "user"
                ? "justify-self-end rounded-lg bg-accent px-4 py-2.5 text-sm text-white sm:max-w-[80%]"
                : "card whitespace-pre-wrap text-sm leading-relaxed text-ink-soft sm:max-w-[90%]"
            }
          >
            {turn.content}
          </div>
        ))}

        {busy ? (
          <p className="text-sm text-ink-muted" aria-live="polite">
            {t.chat.thinking}
          </p>
        ) : null}

        {error ? (
          <Callout tone="warning" title={error}>
            {null}
          </Callout>
        ) : null}

        <div ref={endRef} />
      </div>

      <form onSubmit={send} className="mt-4 flex flex-wrap items-end gap-2">
        <label htmlFor="chat-input" className="sr-only">
          {t.chat.placeholder}
        </label>
        <textarea
          id="chat-input"
          rows={2}
          className="field min-w-0 flex-1 resize-y"
          placeholder={t.chat.placeholder}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            // Enter sends, Shift+Enter breaks the line. A question is usually
            // one line, and reaching for the mouse to ask it is friction.
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void send(event as unknown as React.FormEvent);
            }
          }}
        />
        <button
          type="submit"
          disabled={busy || !draft.trim()}
          className="min-h-[44px] cursor-pointer rounded-md bg-accent px-4 text-sm font-medium
                     text-white transition-colors duration-200 hover:bg-accent/90
                     disabled:cursor-not-allowed disabled:opacity-60"
        >
          {busy ? t.chat.thinking : t.chat.send}
        </button>
        {turns.length ? (
          <button
            type="button"
            onClick={() => {
              setTurns([]);
              setError(null);
            }}
            className="min-h-[44px] cursor-pointer px-2 text-sm text-accent hover:underline"
          >
            {t.chat.clear}
          </button>
        ) : null}
      </form>
    </div>
  );
}
