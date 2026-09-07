import Foundation

/// HomeKit TLV8: tag, length, value; values over 255 bytes are split across repeated tags.
enum TLV {
    static let method: UInt8 = 0x00, identifier: UInt8 = 0x01, salt: UInt8 = 0x02, publicKey: UInt8 = 0x03,
               proof: UInt8 = 0x04, encryptedData: UInt8 = 0x05, seqNo: UInt8 = 0x06, error: UInt8 = 0x07,
               signature: UInt8 = 0x0A, name: UInt8 = 0x11

    static func write(_ items: [(UInt8, Data)]) -> Data {
        var out = Data()
        for (tag, value) in items {
            var pos = 0
            repeat {
                let n = min(255, value.count - pos)
                out.append(tag); out.append(UInt8(n)); out.append(value[value.startIndex + pos ..< value.startIndex + pos + n])
                pos += n
            } while pos < value.count
        }
        return out
    }

    static func read(_ data: Data) -> [UInt8: Data] {
        var out: [UInt8: Data] = [:]
        var p = data.startIndex
        while p + 1 < data.endIndex {
            let tag = data[p], len = Int(data[p + 1])
            let v = data[(p + 2) ..< min(p + 2 + len, data.endIndex)]
            out[tag, default: Data()].append(v)
            p += 2 + len
        }
        return out
    }
}
