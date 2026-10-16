import { Decoder, Encoder } from 'socket.io-parser';
import { decodeStrict } from './strictJson';

// socket.io-client's packet parser, with the protocol's strict decoding
// (schema amendment S2). The stock decoder runs JSON.parse over every packet,
// which keeps the last of two duplicate keys and accepts overflowing numbers;
// a frame could then mean something different here than to the server. This
// decoder parses packet bodies with decodeStrict instead, and refuses binary
// packets, which the VTT protocol never uses.
//
// socket.io-parser 4.2.x routes every body through Decoder#tryParse. It is
// declared private, so it is shadowed on the instance rather than overridden;
// returning false makes the stock code reject the packet ("invalid payload").

const BINARY_EVENT = '5';
const BINARY_ACK = '6';

export class StrictDecoder extends Decoder {
  constructor() {
    super();
    (this as unknown as { tryParse(str: string): unknown }).tryParse = (str: string) => {
      try {
        return decodeStrict(str);
      } catch {
        return false;
      }
    };
  }

  override add(obj: unknown): void {
    if (typeof obj !== 'string') throw new Error('binary packets are not accepted');
    if (obj.startsWith(BINARY_EVENT) || obj.startsWith(BINARY_ACK)) {
      throw new Error('binary packets are not accepted');
    }
    super.add(obj);
  }
}

export const strictParser = { Encoder, Decoder: StrictDecoder };
