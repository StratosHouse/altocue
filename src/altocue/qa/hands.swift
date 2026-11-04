// Finds hands in image files with Apple Vision, on the device. One JSON line per image.
// Usage: hands <image> [<image> ...]
// Each hand is the box around the joints Vision is fairly sure of, from the top left, 0 to 1.
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
    FileHandle.standardError.write("usage: hands <image> [<image> ...]\n".data(using: .utf8)!)
    exit(2)
}

for path in paths {
    guard let image = NSImage(contentsOfFile: path),
          let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
        emit(["file": path, "error": "unreadable"])
        continue
    }
    let request = VNDetectHumanHandPoseRequest()
    request.maximumHandCount = 4
    do {
        try VNImageRequestHandler(cgImage: cg, options: [:]).perform([request])
    } catch {
        emit(["file": path, "error": "vision"])
        continue
    }
    var boxes: [[Double]] = []
    for observation in request.results ?? [] {
        guard let points = try? observation.recognizedPoints(.all) else { continue }
        let seen = points.values.filter { $0.confidence > 0.3 }
        if seen.isEmpty { continue }
        let xs = seen.map { Double($0.location.x) }
        let ys = seen.map { Double($0.location.y) }
        // Vision's origin is bottom left. Everything downstream reads from the top left.
        boxes.append([xs.min()!, 1 - ys.max()!, xs.max()! - xs.min()!, ys.max()! - ys.min()!])
    }
    emit(["file": path, "hands": boxes])
}
