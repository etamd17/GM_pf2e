import { useRef, useState } from 'react';
import type { GmBoot } from '../live/boot';

// Choosing a scene background or token art (M6): the file is uploaded to the
// campaign's asset store, and the caller then issues the command that points
// the scene or token at the returned asset id. The server decides whether the
// file is acceptable (by its bytes); the checks here only save a pointless
// upload and give a quicker message.

export const ACCEPTED = ['image/png', 'image/jpeg', 'image/webp'] as const;
export const MAX_BYTES = 25 * 1024 * 1024;

export type UploadResult = { ok: true; id: string } | { ok: false; error: string };

export async function uploadAsset(boot: Pick<GmBoot, 'campaignId' | 'csrf'>, file: Blob, fetcher: typeof fetch = fetch): Promise<UploadResult> {
  if (!(ACCEPTED as readonly string[]).includes(file.type)) return { ok: false, error: 'Choose a PNG, JPEG or WebP image.' };
  if (file.size > MAX_BYTES) return { ok: false, error: 'Images must be 25 MB or smaller.' };
  try {
    const response = await fetcher(`/vtt/api/assets?campaignId=${encodeURIComponent(boot.campaignId)}`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': file.type, 'X-CSRF-Token': boot.csrf },
      body: file,
    });
    const data = (await response.json().catch(() => null)) as { asset?: { id?: unknown }; error?: unknown } | null;
    if (response.ok && typeof data?.asset?.id === 'string') return { ok: true, id: data.asset.id };
    if (response.status === 503) return { ok: false, error: 'The server is busy. Try again in a few seconds.' };
    if (response.status === 403) return { ok: false, error: 'You can no longer change this campaign. Sign in again.' };
    return { ok: false, error: typeof data?.error === 'string' ? capitalize(data.error) + '.' : 'The image could not be uploaded.' };
  } catch {
    return { ok: false, error: 'Could not reach the server. Check the connection and try again.' };
  }
}

const capitalize = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

export interface ImagePickerProps {
  label: string;
  boot: Pick<GmBoot, 'campaignId' | 'csrf'>;
  current: string | null; // the asset id in use, if any
  previewUrl: string | null;
  disabled: boolean;
  onChange(assetId: string | null): void;
  fetcher?: typeof fetch;
}

export function ImagePicker({ label, boot, current, previewUrl, disabled, onChange, fetcher }: ImagePickerProps) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function picked(file: File | undefined) {
    if (!file) return;
    setBusy(true);
    setError(null);
    const result = await uploadAsset(boot, file, fetcher);
    setBusy(false);
    if (input.current) input.current.value = ''; // the same file again is a fresh choice
    if (!result.ok) {
      setError(result.error);
      return;
    }
    if (result.id !== current) onChange(result.id);
  }

  return (
    <div className="vtt-field vtt-image-picker">
      <span>{label}</span>
      {previewUrl && current && <img className="vtt-image-preview" src={previewUrl} alt="" width={48} height={48} />}
      <div className="vtt-image-actions">
        <button type="button" disabled={disabled || busy} onClick={() => input.current?.click()}>
          {busy ? 'Uploading...' : current ? 'Replace' : 'Choose image'}
        </button>
        {current && (
          <button type="button" disabled={disabled || busy} onClick={() => onChange(null)}>
            Remove
          </button>
        )}
      </div>
      <input
        ref={input}
        type="file"
        accept={ACCEPTED.join(',')}
        hidden
        aria-label={`${label} file`}
        onChange={(e) => void picked(e.target.files?.[0])}
      />
      {error && (
        <p className="vtt-pairing-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
