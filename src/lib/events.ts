import { useEffect } from "react";

/** Subscribes once, on mount, and unsubscribes on unmount: `subscribe` starts listening
 * (a Tauri event, a window or webview change) and resolves to its stop function. */
export function useSubscription(subscribe: () => Promise<() => void>) {
  useEffect(() => {
    const stop = subscribe();
    return () => void stop.then((stop) => stop());
    // Once: the listeners read the stores when called.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}
