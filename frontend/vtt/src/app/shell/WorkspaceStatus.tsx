interface Props {
  pending: ReadonlyArray<{ state: string }>;
  zoom: number;
  status: string;
}

export function WorkspaceStatus({ pending, zoom, status }: Props) {
  return (<>{pending.length > 0 && (
          <span className="vtt-pending" aria-live="polite">
            {pending.some((p) => p.state !== 'sent') ? 'Waiting to send' : 'Saving'} ({pending.length})
          </span>
        )}
        <span className="vtt-zoom" aria-label="Zoom level">
          {Math.round(zoom * 100)}%
        </span>
        <span className={`vtt-conn vtt-conn--${status}`}>{status}</span></>);
}
