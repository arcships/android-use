import Foundation
import Vision

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.recognitionLanguages = ["zh-Hans", "en-US"]
request.usesLanguageCorrection = true
try VNImageRequestHandler(url: URL(fileURLWithPath: CommandLine.arguments[1])).perform([request])
let rows = (request.results ?? []).prefix(500).compactMap { observation -> [String: Any]? in
    guard let text = observation.topCandidates(1).first else { return nil }
    let r = observation.boundingBox
    return ["text": text.string, "confidence": Double(text.confidence),
            "box": [Double(r.minX), Double(1 - r.maxY), Double(r.width), Double(r.height)]]
}
let data = try JSONSerialization.data(withJSONObject: rows)
print(String(data: data, encoding: .utf8)!)
