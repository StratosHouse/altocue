// Reads text from image files with Apple Vision, on the device. One JSON line per text box.
// Usage: ocr <image> [<image> ...]
// Language correction stays off so brand words are not "corrected" into dictionary words.
// Vision ignores customWords while correction is off, so none are passed.
import AppKit
import Foundation
import Vision

func emit(_ record: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: record),
       let line = String(data: data, encoding: .utf8) {
        print(line)
    }
}

let paths = CommandLine.arguments.dropFirst()
if paths.isEmpty {
    FileHandle.standardError.write("usage: ocr <image> [<image> ...]\n".data(using: .utf8)!)
    exit(2)
}

for path in paths {
    guard let image = NSImage(contentsOfFile: path),
          let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
        emit(["file": path, "error": "unreadable"])
        continue
    }
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = false
    request.minimumTextHeight = 0.008
    do {
        try VNImageRequestHandler(cgImage: cg, options: [:]).perform([request])
    } catch {
        emit(["file": path, "error": "vision"])
        continue
    }
    for observation in request.results ?? [] {
        guard let top = observation.topCandidates(1).first else { continue }
        let b = observation.boundingBox
        // Vision's origin is bottom left. Everything downstream reads from the top left.
        emit([
            "file": path,
            "text": top.string,
            "confidence": Double(top.confidence),
            "box": [Double(b.origin.x), 1 - Double(b.origin.y) - Double(b.size.height), Double(b.size.width), Double(b.size.height)],
        ])
    }
}
