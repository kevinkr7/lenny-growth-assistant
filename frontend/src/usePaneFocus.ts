import { useEffect, useRef } from "react";

// Side panels remain complementary on desktop; overlays contain focus on mobile.
export function usePaneFocus(close: () => void) {
  const ref = useRef<HTMLElement>(null);
  const closeRef = useRef(close);
  closeRef.current = close;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const pane = ref.current;
    pane?.querySelector<HTMLButtonElement>("button")?.focus();
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        closeRef.current();
      }
      if (
        event.key !== "Tab" ||
        !window.matchMedia("(max-width: 1000px)").matches
      )
        return;
      const items = Array.from(
        pane?.querySelectorAll<HTMLElement>(
          'button:not(:disabled):not([tabindex="-1"]), a[href], summary, iframe, [tabindex="0"]',
        ) || [],
      ).filter((el) => el.getClientRects().length > 0);
      const first = items[0],
        last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    };
    pane?.addEventListener("keydown", keydown);
    return () => {
      pane?.removeEventListener("keydown", keydown);
      if (previous?.isConnected) previous.focus();
    };
  }, []);
  return ref;
}
