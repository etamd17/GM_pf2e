// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ImagePicker, uploadAsset } from './ImagePicker';

afterEach(cleanup);

const boot = { campaignId: 'camp_a', csrf: 'csrf-x' };
const png = (size = 10) => new File([new Uint8Array(size)], 'map.png', { type: 'image/png' });
const answer = (status: number, body: unknown) =>
  vi.fn(async () => ({ ok: status < 300, status, json: async () => body }) as Response) as unknown as typeof fetch;

describe('uploadAsset', () => {
  it('posts the raw file with its type and the CSRF token, and returns the asset id', async () => {
    const fetcher = answer(201, { asset: { id: 'asset_abc' } });
    const file = png();
    expect(await uploadAsset(boot, file, fetcher)).toEqual({ ok: true, id: 'asset_abc' });
    expect(fetcher).toHaveBeenCalledWith('/vtt/api/assets?campaignId=camp_a', {
      method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'image/png', 'X-CSRF-Token': 'csrf-x' }, body: file,
    });
  });

  it('refuses the wrong type or size before uploading', async () => {
    const fetcher = answer(201, {});
    expect(await uploadAsset(boot, new File(['x'], 'a.gif', { type: 'image/gif' }), fetcher)).toEqual({
      ok: false, error: 'Choose a PNG, JPEG or WebP image.',
    });
    const huge = { type: 'image/png', size: 26 * 1024 * 1024 } as Blob;
    expect((await uploadAsset(boot, huge, fetcher)).ok).toBe(false);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('says what the server said, or that it is busy', async () => {
    expect(await uploadAsset(boot, png(), answer(400, { error: 'that file is not a readable PNG, JPEG or WebP image' }))).toEqual({
      ok: false, error: 'That file is not a readable PNG, JPEG or WebP image.',
    });
    expect(await uploadAsset(boot, png(), answer(503, { error: 'temporarily-unavailable' }))).toEqual({
      ok: false, error: 'The server is busy. Try again in a few seconds.',
    });
    const offline = vi.fn(async () => { throw new TypeError('offline'); }) as unknown as typeof fetch;
    expect((await uploadAsset(boot, png(), offline)).ok).toBe(false);
  });
});

describe('ImagePicker', () => {
  const choose = async (file: File) => {
    await act(async () => {
      fireEvent.change(screen.getByLabelText('Art file'), { target: { files: [file] } });
      await new Promise((r) => setTimeout(r, 0));
    });
  };

  it('uploads the chosen file, then reports the new asset', async () => {
    const onChange = vi.fn();
    render(<ImagePicker label="Art" boot={boot} current={null} previewUrl={null} disabled={false} onChange={onChange}
      fetcher={answer(201, { asset: { id: 'asset_new' } })} />);
    expect(screen.getByRole('button', { name: 'Choose image' })).toBeTruthy();
    await choose(png());
    expect(onChange).toHaveBeenCalledWith('asset_new');
  });

  it('the same image again changes nothing; Remove clears it', async () => {
    const onChange = vi.fn();
    render(<ImagePicker label="Art" boot={boot} current="asset_same" previewUrl="/vtt/media/camp_a/asset_same"
      disabled={false} onChange={onChange} fetcher={answer(200, { asset: { id: 'asset_same' } })} />);
    await choose(png());
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Remove' }));
    expect(onChange).toHaveBeenCalledWith(null);
  });

  it('a refused upload shows why and issues nothing', async () => {
    const onChange = vi.fn();
    render(<ImagePicker label="Art" boot={boot} current={null} previewUrl={null} disabled={false} onChange={onChange}
      fetcher={answer(400, { error: 'images must be at most 16384 pixels on each side' })} />);
    await choose(png());
    expect(screen.getByRole('alert').textContent).toBe('Images must be at most 16384 pixels on each side.');
    expect(onChange).not.toHaveBeenCalled();
  });
});
