import { notifications } from "@mantine/notifications";
import { ApiError } from "../api/client";

/** Server-supplied detail for an ApiError, else whatever the thrown value
 * stringifies to (network failures, unexpected non-Error throws). */
export function errorMessage(err: unknown): string {
  return err instanceof ApiError ? err.message : String(err);
}

/** Red toast for a failed API call -- the app's one error-reporting channel. */
export function showApiError(title: string, err: unknown) {
  notifications.show({ color: "red", title, message: errorMessage(err) });
}
