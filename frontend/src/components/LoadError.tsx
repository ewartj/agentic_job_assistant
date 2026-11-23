/** Why something couldn't be loaded, with a button to try again. */
export function LoadError({ error, loading, onRetry }: { error: string; loading: boolean; onRetry: () => void }) {
  return (
    <div className="row" role="alert">
      <p className="error">{error}</p>
      {/* aria-disabled, not disabled: the button keeps keyboard focus while it retries */}
      <button type="button" onClick={() => loading || onRetry()} aria-disabled={loading}>
        {loading ? "Trying again..." : "Try again"}
      </button>
    </div>
  );
}
