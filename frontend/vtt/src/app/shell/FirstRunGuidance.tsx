export function FirstRunGuidance({enabled, onNew, onPair}: {enabled: boolean; onNew(): void; onPair(): void}) {
  return <section className="vtt-first-run" aria-labelledby="vtt-first-run-title">
    <h1 id="vtt-first-run-title">Set up your first scene</h1>
    <ol><li>Create a scene.</li><li>Add a background in Scene.</li><li>Place creatures and link them to the tracker.</li><li>Show the scene on your TV.</li></ol>
    <div className="vtt-guidance-actions">
      <button type="button" disabled={!enabled} onClick={onNew}>Create first scene</button>
      <button type="button" onClick={onPair}>Pair a TV</button>
    </div>
    <p>Editing a scene does not change what is on the TV.</p>
  </section>;
}
