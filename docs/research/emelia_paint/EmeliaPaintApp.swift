import SwiftUI
import AVFoundation
import UIKit

// ═══════════════════════════════════════════════════════════
// Emelia's Magical Studio — A magic canvas for tiny hands
//
// Design: every touch = delight. No wrong moves.
// No visible UI. Parent controls behind 3s two-corner hold.
// Auto-rainbow, musical drawing, sparkle trails,
// spoken sticker names, shake-to-celebrate, peekaboo.
// ═══════════════════════════════════════════════════════════

@main
struct EmeliaPaintApp: App {
    var body: some Scene {
        WindowGroup {
            EmeliaPaintView()
                .statusBarHidden()
                .persistentSystemOverlays(.hidden)
        }
    }
}

// MARK: - Shake Detection

extension NSNotification.Name {
    static let deviceDidShake = NSNotification.Name("deviceDidShake")
}

extension UIWindow {
    open override func motionEnded(_ motion: UIEvent.EventSubtype, with event: UIEvent?) {
        if motion == .motionShake {
            NotificationCenter.default.post(name: .deviceDidShake, object: nil)
        }
        super.motionEnded(motion, with: event)
    }
}

// MARK: - Models

struct Stroke: Identifiable {
    let id = UUID()
    var points: [CGPoint]
    var hueStart: Double
    var lineWidth: CGFloat
}

struct PlacedSticker: Identifiable {
    let id = UUID()
    var position: CGPoint
    var emoji: String
    var rotation: Double
    var celebrating = false
}

struct Sparkle: Identifiable {
    let id = UUID()
    var position: CGPoint
    var hue: Double
    var size: CGFloat
}

// MARK: - Background Styles

enum BackgroundStyle: String, CaseIterable {
    case blank = "Blank"
    case softPink = "Pink"
    case softBlue = "Blue"
    case softGreen = "Green"
    case dotGrid = "Dots"
    case bigShapes = "Shapes"
}

enum BorderTheme: String, CaseIterable {
    case none = "None"
    case jungle = "Jungle"
    case forest = "Forest"
    case waterfall = "Waterfall"
    case underwater = "Underwater"
    case fairy = "Fairy"
    case starry = "Starry"

    var assetName: String? {
        switch self {
        case .none: return nil
        case .jungle: return "border_jungle"
        case .forest: return "border_forest"
        case .waterfall: return "border_waterfall"
        case .underwater: return "border_underwater"
        case .fairy: return "border_fairy"
        case .starry: return "border_starry"
        }
    }
}

// MARK: - Main View

struct EmeliaPaintView: View {

    @State private var strokes: [Stroke] = []
    @State private var currentPoints: [CGPoint] = []
    @State private var hueOffset: Double = 0
    @State private var stickers: [PlacedSticker] = []
    @State private var sparkles: [Sparkle] = []
    @State private var showParentControls = false
    @State private var showBorderPicker = false
    @State private var showSplash = true
    @State private var isDirty = false
    @State private var touchStartTime: Date?
    @State private var lastActivityTime = Date()
    @State private var speakStickers = true
    @State private var backgroundStyle: BackgroundStyle = .blank
    @State private var borderTheme: BorderTheme = .none
    @StateObject private var tone = TonePlayer()
    @StateObject private var voice = StickerVoice()

    private let lineWidth: CGFloat = 28
    private let tapThreshold: CGFloat = 12
    private let peekabooThreshold: TimeInterval = 1.5
    private let maxStrokes = 30
    private let maxStickers = 20

    private let customStickers = [
        "sticker_elephant", "sticker_giraffe", "sticker_dino", "sticker_penguin",
        "sticker_hippo", "sticker_lion", "sticker_ladybug", "sticker_hedgehog",
        "sticker_rainbow", "sticker_wand", "sticker_treasure", "sticker_mushroom",
        "sticker_gem", "sticker_balloon", "sticker_potion", "sticker_crown",
        "sticker_icecream", "sticker_donut", "sticker_cupcake", "sticker_watermelon",
        "sticker_icecream2", "sticker_cookie", "sticker_lollipop", "sticker_pizza",
    ]

    private let stickerNames: [String: String] = [
        "sticker_elephant": "Elephant!", "sticker_giraffe": "Giraffe!",
        "sticker_dino": "Dinosaur!", "sticker_penguin": "Otter!",
        "sticker_hippo": "Hippo!", "sticker_lion": "Lion!",
        "sticker_ladybug": "Porcupine!", "sticker_hedgehog": "Hedgehog!",
        "sticker_rainbow": "Rainbow!", "sticker_wand": "Magic wand!",
        "sticker_treasure": "Treasure!", "sticker_mushroom": "Mushroom!",
        "sticker_gem": "Gem!", "sticker_balloon": "Balloon!",
        "sticker_potion": "Poe shun!", "sticker_crown": "Crown!",
        "sticker_icecream": "Ice cream!", "sticker_donut": "Donut!",
        "sticker_cupcake": "Cupcake!", "sticker_watermelon": "Watermelon!",
        "sticker_icecream2": "Ice cream!", "sticker_cookie": "Cookie!",
        "sticker_lollipop": "Lollipop!", "sticker_pizza": "Pizza!",
    ]

    var body: some View {
        ZStack {

            backgroundView.ignoresSafeArea()

            // Border BEHIND the canvas — drawing goes on top
            if let asset = borderTheme.assetName {
                GeometryReader { geo in
                    Image(asset)
                        .resizable()
                        .scaledToFit()
                        .frame(width: geo.size.width, height: geo.size.height)
                }
                .ignoresSafeArea()
                .allowsHitTesting(false)
            }

            Canvas { context, _ in
                let total = strokes.count
                for (idx, stroke) in strokes.enumerated() {
                    let age = Double(total - idx) / Double(max(total, 1))
                    let fade = max(0.15, 1.0 - age * 0.7)
                    drawRainbow(stroke, in: context, opacity: fade)
                }
                if !currentPoints.isEmpty {
                    drawRainbow(
                        Stroke(points: currentPoints, hueStart: hueOffset,
                               lineWidth: lineWidth),
                        in: context, opacity: 1.0
                    )
                }
            }

            ForEach(Array(sparkles.enumerated()), id: \.element.id) { idx, s in
                Circle()
                    .fill(Color(hue: s.hue.truncatingRemainder(dividingBy: 1),
                                saturation: 0.7, brightness: 1))
                    .frame(width: s.size, height: s.size)
                    .opacity(Double(idx) / Double(max(sparkles.count, 1)))
                    .position(s.position)
                    .allowsHitTesting(false)
            }

            ForEach(stickers) { sticker in
                BouncingStickerView(sticker: sticker)
            }

            // Bottom bar — clear (left), border picker (right)
            VStack {
                Spacer()
                HStack {
                    // Clear button
                    Button {
                        withAnimation(.spring(response: 0.4, dampingFraction: 0.5)) {
                            strokes.removeAll()
                            stickers.removeAll()
                            sparkles.removeAll()
                        }
                        tone.playCelebration()
                        UIImpactFeedbackGenerator(style: .heavy).impactOccurred()
                    } label: {
                        Image("icon_clear")
                            .resizable()
                            .frame(width: 60, height: 60)
                            .padding(12)
                            .background(
                                Circle()
                                    .fill(.white.opacity(0.6))
                                    .frame(width: 80, height: 80)
                            )
                    }
                    .padding(.leading, 20)

                    Spacer()

                    // Border picker button — fanned cards
                    Button {
                        showBorderPicker = true
                        tone.playChirp()
                        UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                    } label: {
                        ZStack {
                            RoundedRectangle(cornerRadius: 6)
                                .fill(Color.green.opacity(0.4))
                                .frame(width: 36, height: 48)
                                .rotationEffect(.degrees(-15))
                            RoundedRectangle(cornerRadius: 6)
                                .fill(Color.purple.opacity(0.4))
                                .frame(width: 36, height: 48)
                                .rotationEffect(.degrees(0))
                            RoundedRectangle(cornerRadius: 6)
                                .fill(Color.blue.opacity(0.4))
                                .frame(width: 36, height: 48)
                                .rotationEffect(.degrees(15))
                            Text("🖼️")
                                .font(.system(size: 22))
                        }
                        .padding(12)
                        .background(
                            Circle()
                                .fill(.white.opacity(0.6))
                                .frame(width: 80, height: 80)
                        )
                    }
                    .padding(.trailing, 20)
                }
                .padding(.bottom, 20)
            }

            // Border picker modal
            if showBorderPicker {
                borderPickerOverlay
            }

            // Parent lock — requires 3s hold, nearly invisible
            VStack {
                HStack {
                    Spacer()
                    Image(systemName: "gearshape.fill")
                        .font(.system(size: 18))
                        .foregroundColor(.gray.opacity(0.35))
                        .padding(20)
                        .contentShape(Rectangle())
                        .onLongPressGesture(minimumDuration: 3) {
                            showParentControls = true
                        }
                }
                Spacer()
            }
            .padding(.top, 40)

            if showSplash { splashOverlay }
            if showParentControls { parentControls }
        }
        .gesture(canvasGesture)
        .ignoresSafeArea()
        .onReceive(NotificationCenter.default.publisher(for: .deviceDidShake)) { _ in
            guard !showParentControls, !showSplash else { return }
            celebrate()
        }
        .onAppear {
            DispatchQueue.main.asyncAfter(deadline: .now() + 3.0) {
                withAnimation(.easeOut(duration: 0.8)) {
                    showSplash = false
                }
            }
        }
    }

    // MARK: - Gesture

    private var canvasGesture: some Gesture {
        DragGesture(minimumDistance: 0)
            .onChanged { value in
                guard !showParentControls, !showSplash else { return }
                if currentPoints.isEmpty {
                    touchStartTime = Date()
                }
                currentPoints.append(value.location)
                tone.updatePitch(for: value.location,
                                 in: UIScreen.main.bounds.size)
                if currentPoints.count % 3 == 0 {
                    addSparkle(at: value.location)
                }
                UIImpactFeedbackGenerator(style: .light)
                    .impactOccurred(intensity: 0.35)
            }
            .onEnded { value in
                guard !showParentControls, !showSplash else { return }
                tone.stop()
                lastActivityTime = Date()

                let movement = maxMovement(currentPoints)
                let holdDuration = Date().timeIntervalSince(touchStartTime ?? Date())

                if movement < tapThreshold && holdDuration >= peekabooThreshold {
                    placePeekaboo(at: value.location)
                } else if movement < tapThreshold {
                    placeSticker(at: value.location)
                    placeDot(at: value.location)
                } else {
                    strokes.append(Stroke(points: currentPoints,
                                          hueStart: hueOffset,
                                          lineWidth: lineWidth))
                }

                // Auto-prune so canvas doesn't become a jumbled mess
                if strokes.count > maxStrokes {
                    strokes.removeFirst(strokes.count - maxStrokes)
                }
                if stickers.count > maxStickers {
                    stickers.removeFirst(stickers.count - maxStickers)
                }

                currentPoints.removeAll()
                touchStartTime = nil
                hueOffset += 0.07
                if hueOffset > 1 { hueOffset -= 1 }
                isDirty = true
                burstSparkles(at: value.location)
            }
    }

    // MARK: - Rainbow Drawing

    private func drawRainbow(_ stroke: Stroke, in ctx: GraphicsContext,
                              opacity: Double = 1.0) {
        guard stroke.points.count > 1 else { return }
        for i in 1..<stroke.points.count {
            var seg = Path()
            seg.move(to: stroke.points[i - 1])
            seg.addLine(to: stroke.points[i])

            let t = Double(i) / Double(stroke.points.count)
            let hue = (stroke.hueStart + t * 0.3)
                .truncatingRemainder(dividingBy: 1)
            let color = Color(hue: hue, saturation: 0.75, brightness: 1)
                .opacity(opacity)

            ctx.stroke(seg, with: .color(color),
                       style: StrokeStyle(lineWidth: stroke.lineWidth,
                                          lineCap: .round, lineJoin: .round))
        }
    }

    // MARK: - Stickers

    private func placeSticker(at point: CGPoint) {
        let sticker = customStickers.randomElement()!
        stickers.append(PlacedSticker(
            position: point, emoji: sticker,
            rotation: .random(in: -20...20)
        ))
        tone.playChirp()
        UIImpactFeedbackGenerator(style: .medium).impactOccurred()

        if speakStickers, let name = stickerNames[sticker] {
            voice.say(name)
        }
    }

    // MARK: - Peekaboo Easter Egg

    private func placePeekaboo(at point: CGPoint) {
        let monkeyHide = PlacedSticker(
            position: point, emoji: "🙈",
            rotation: .random(in: -10...10)
        )
        let hideId = monkeyHide.id
        stickers.append(monkeyHide)
        UIImpactFeedbackGenerator(style: .medium).impactOccurred()

        if speakStickers {
            voice.say("Peekaboo!")
        }

        // After 1.2s, swap to revealed monkey
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) {
            if let idx = stickers.firstIndex(where: { $0.id == hideId }) {
                withAnimation(.spring(response: 0.3, dampingFraction: 0.5)) {
                    stickers[idx].emoji = "🐵"
                }
            }
            tone.playChirp()
        }
    }

    // MARK: - Shake to Celebrate

    private func celebrate() {
        UINotificationFeedbackGenerator().notificationOccurred(.success)
        tone.playCelebration()

        // Big sparkle burst from center
        let center = CGPoint(x: UIScreen.main.bounds.midX,
                             y: UIScreen.main.bounds.midY)
        for _ in 0..<30 {
            sparkles.append(Sparkle(
                position: CGPoint(x: center.x + .random(in: -200...200),
                                  y: center.y + .random(in: -200...200)),
                hue: .random(in: 0...1),
                size: .random(in: 8...20)
            ))
        }
        if sparkles.count > 300 { sparkles.removeFirst(sparkles.count - 150) }

        // Bounce all stickers
        for i in stickers.indices {
            withAnimation(.spring(response: 0.3, dampingFraction: 0.4).delay(Double(i) * 0.03)) {
                stickers[i].celebrating = true
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.5 + Double(i) * 0.03) {
                if i < stickers.count {
                    withAnimation(.spring(response: 0.2, dampingFraction: 0.6)) {
                        stickers[i].celebrating = false
                    }
                }
            }
        }
    }

    // MARK: - Dots & Sparkles

    private func placeDot(at point: CGPoint) {
        strokes.append(Stroke(
            points: [point, CGPoint(x: point.x + 0.5, y: point.y + 0.5)],
            hueStart: hueOffset,
            lineWidth: lineWidth * 2.5
        ))
    }

    private func addSparkle(at point: CGPoint) {
        if sparkles.count > 150 { sparkles.removeFirst(30) }
        sparkles.append(Sparkle(
            position: CGPoint(x: point.x + .random(in: -15...15),
                              y: point.y + .random(in: -15...15)),
            hue: hueOffset + .random(in: -0.1...0.1),
            size: .random(in: 4...10)
        ))
    }

    private func burstSparkles(at point: CGPoint) {
        for _ in 0..<12 {
            sparkles.append(Sparkle(
                position: CGPoint(x: point.x + .random(in: -60...60),
                                  y: point.y + .random(in: -60...60)),
                hue: .random(in: 0...1),
                size: .random(in: 6...14)
            ))
        }
        if sparkles.count > 200 { sparkles.removeFirst(sparkles.count - 150) }
    }

    // MARK: - Backgrounds

    @ViewBuilder
    private var backgroundView: some View {
        switch backgroundStyle {
        case .blank:
            Color(white: 0.98)
        case .softPink:
            LinearGradient(colors: [Color(red: 1, green: 0.92, blue: 0.95),
                                    Color(red: 1, green: 0.85, blue: 0.9)],
                           startPoint: .top, endPoint: .bottom)
        case .softBlue:
            LinearGradient(colors: [Color(red: 0.9, green: 0.95, blue: 1),
                                    Color(red: 0.82, green: 0.9, blue: 1)],
                           startPoint: .top, endPoint: .bottom)
        case .softGreen:
            LinearGradient(colors: [Color(red: 0.9, green: 1, blue: 0.92),
                                    Color(red: 0.82, green: 0.95, blue: 0.85)],
                           startPoint: .top, endPoint: .bottom)
        case .dotGrid:
            ZStack {
                Color(white: 0.98)
                DotGridView()
            }
        case .bigShapes:
            ZStack {
                Color(white: 0.98)
                BigShapesView()
            }
        }
    }

    // MARK: - Background Preview Swatches

    @ViewBuilder
    private func bgPreview(for style: BackgroundStyle) -> some View {
        switch style {
        case .blank:
            ZStack {
                Color(white: 0.98)
                Text("✨").font(.system(size: 24))
            }
        case .softPink:
            LinearGradient(colors: [Color(red: 1, green: 0.92, blue: 0.95),
                                    Color(red: 1, green: 0.85, blue: 0.9)],
                           startPoint: .top, endPoint: .bottom)
        case .softBlue:
            LinearGradient(colors: [Color(red: 0.9, green: 0.95, blue: 1),
                                    Color(red: 0.82, green: 0.9, blue: 1)],
                           startPoint: .top, endPoint: .bottom)
        case .softGreen:
            LinearGradient(colors: [Color(red: 0.9, green: 1, blue: 0.92),
                                    Color(red: 0.82, green: 0.95, blue: 0.85)],
                           startPoint: .top, endPoint: .bottom)
        case .dotGrid:
            ZStack {
                Color(white: 0.95)
                Text("•••").font(.system(size: 14)).foregroundColor(.gray)
            }
        case .bigShapes:
            ZStack {
                Color(white: 0.95)
                Circle().stroke(.gray.opacity(0.4), lineWidth: 2)
                    .frame(width: 25, height: 25)
            }
        }
    }

    // MARK: - Helpers

    private func maxMovement(_ pts: [CGPoint]) -> CGFloat {
        guard let first = pts.first else { return 0 }
        return pts.reduce(0) { max($0, hypot($1.x - first.x, $1.y - first.y)) }
    }

    // MARK: - Save

    private func saveDrawing() {
        let size = UIScreen.main.bounds.size
        let renderer = UIGraphicsImageRenderer(size: size)
        let image = renderer.image { ctx in
            let gc = ctx.cgContext
            gc.setFillColor(UIColor(white: 0.98, alpha: 1).cgColor)
            gc.fill(CGRect(origin: .zero, size: size))

            for stroke in strokes {
                gc.setLineWidth(stroke.lineWidth)
                gc.setLineCap(.round)
                gc.setLineJoin(.round)
                for i in 1..<stroke.points.count {
                    let t = Double(i) / Double(stroke.points.count)
                    let hue = (stroke.hueStart + t * 0.3)
                        .truncatingRemainder(dividingBy: 1)
                    gc.setStrokeColor(UIColor(hue: hue, saturation: 0.75,
                                              brightness: 1, alpha: 1).cgColor)
                    gc.beginPath()
                    gc.move(to: stroke.points[i - 1])
                    gc.addLine(to: stroke.points[i])
                    gc.strokePath()
                }
            }

            for sticker in stickers {
                if sticker.emoji.hasPrefix("sticker_"),
                   let uiImage = UIImage(named: sticker.emoji) {
                    let sz: CGFloat = 80
                    let origin = CGPoint(x: sticker.position.x - sz / 2,
                                         y: sticker.position.y - sz / 2)
                    uiImage.draw(in: CGRect(origin: origin,
                                            size: CGSize(width: sz, height: sz)))
                } else {
                    let text = sticker.emoji as NSString
                    let attrs: [NSAttributedString.Key: Any] = [
                        .font: UIFont.systemFont(ofSize: 60),
                    ]
                    let textSize = text.size(withAttributes: attrs)
                    let origin = CGPoint(x: sticker.position.x - textSize.width / 2,
                                         y: sticker.position.y - textSize.height / 2)
                    text.draw(at: origin, withAttributes: attrs)
                }
            }
        }
        UIImageWriteToSavedPhotosAlbum(image, nil, nil, nil)
    }

    // MARK: - Splash

    private var splashOverlay: some View {
        ZStack {
            Color.white.ignoresSafeArea()
            Image("SplashImage")
                .resizable()
                .scaledToFit()
                .padding(.horizontal, 10)
                .padding(.top, 50)
        }
    }

    // MARK: - Border Picker (kid-facing)

    private var borderPickerOverlay: some View {
        ZStack {
            Color.black.opacity(0.4).ignoresSafeArea()
                .onTapGesture {
                    withAnimation(.spring(response: 0.3)) {
                        showBorderPicker = false
                    }
                }

            VStack(spacing: 20) {
                // Solid color backgrounds + blank
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 12) {
                        ForEach(BackgroundStyle.allCases, id: \.self) { style in
                            Button {
                                backgroundStyle = style
                                borderTheme = .none
                                tone.playChirp()
                                UIImpactFeedbackGenerator(style: .light).impactOccurred()
                            } label: {
                                ZStack {
                                    bgPreview(for: style)
                                        .frame(width: 60, height: 60)
                                        .clipShape(Circle())
                                    if backgroundStyle == style && borderTheme == .none {
                                        Circle()
                                            .stroke(Color.yellow, lineWidth: 4)
                                            .frame(width: 64, height: 64)
                                    }
                                }
                            }
                        }
                    }
                    .padding(.horizontal, 30)
                }

                // Border thumbnails in 2 rows of 3
                LazyVGrid(columns: [
                    GridItem(.flexible(), spacing: 16),
                    GridItem(.flexible(), spacing: 16),
                    GridItem(.flexible(), spacing: 16),
                ], spacing: 16) {
                    ForEach(BorderTheme.allCases.filter { $0 != .none }, id: \.self) { theme in
                        Button {
                            borderTheme = theme
                            tone.playChirp()
                            UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                            if speakStickers {
                                voice.say(theme.rawValue)
                            }
                            withAnimation(.spring(response: 0.3)) {
                                showBorderPicker = false
                            }
                        } label: {
                            if let asset = theme.assetName {
                                Image(asset)
                                    .resizable()
                                    .aspectRatio(2/3, contentMode: .fill)
                                    .frame(height: 180)
                                    .clipShape(RoundedRectangle(cornerRadius: 16))
                                    .overlay(
                                        RoundedRectangle(cornerRadius: 16)
                                            .stroke(borderTheme == theme ? Color.yellow : Color.white.opacity(0.5), lineWidth: borderTheme == theme ? 5 : 2)
                                    )
                                    .shadow(color: .black.opacity(0.3), radius: 8)
                            }
                        }
                    }
                }
                .padding(.horizontal, 30)
            }
            .padding(20)
        }
        .transition(.opacity)
    }

    // MARK: - Parent Controls

    private var parentControls: some View {
        ZStack {
            Color.black.opacity(0.4).ignoresSafeArea()
                .onTapGesture { showParentControls = false }

            VStack(spacing: 14) {
                Text("Parent Controls")
                    .font(.title2.bold())
                    .padding(.bottom, 6)

                Button {
                    saveDrawing()
                    UINotificationFeedbackGenerator()
                        .notificationOccurred(.success)
                    showParentControls = false
                } label: {
                    Label("Save to Photos", systemImage: "square.and.arrow.down")
                        .frame(maxWidth: .infinity)
                }
                .parentBtn(.blue)

                Toggle("Speak Sticker Names", isOn: $speakStickers)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 4)

                VStack(alignment: .leading, spacing: 6) {
                    Text("Background").font(.subheadline.bold())
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 8) {
                            ForEach(BackgroundStyle.allCases, id: \.self) { style in
                                Button(style.rawValue) {
                                    backgroundStyle = style
                                }
                                .font(.caption.bold())
                                .padding(.horizontal, 12)
                                .padding(.vertical, 8)
                                .background(backgroundStyle == style ? Color.blue : Color.gray.opacity(0.3))
                                .foregroundColor(backgroundStyle == style ? .white : .primary)
                                .cornerRadius(8)
                            }
                        }
                    }
                }
                .padding(.horizontal, 8)

                VStack(alignment: .leading, spacing: 6) {
                    Text("Border Theme").font(.subheadline.bold())
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 8) {
                            ForEach(BorderTheme.allCases, id: \.self) { theme in
                                Button(theme.rawValue) {
                                    borderTheme = theme
                                }
                                .font(.caption.bold())
                                .padding(.horizontal, 12)
                                .padding(.vertical, 8)
                                .background(borderTheme == theme ? Color.purple : Color.gray.opacity(0.3))
                                .foregroundColor(borderTheme == theme ? .white : .primary)
                                .cornerRadius(8)
                            }
                        }
                    }
                }
                .padding(.horizontal, 8)

                Button {
                    withAnimation {
                        strokes.removeAll()
                        stickers.removeAll()
                        sparkles.removeAll()
                    }
                    isDirty = false
                    showParentControls = false
                } label: {
                    Label("Clear Canvas", systemImage: "trash")
                        .frame(maxWidth: .infinity)
                }
                .parentBtn(.red)

                Button {
                    showParentControls = false
                } label: {
                    Text("Done").frame(maxWidth: .infinity)
                }
                .parentBtn(.gray)
            }
            .padding(24)
            .frame(width: 280)
            .background(RoundedRectangle(cornerRadius: 20).fill(.white))
            .shadow(radius: 20)
        }
    }
}

// MARK: - Bouncing Sticker View

struct BouncingStickerView: View {
    let sticker: PlacedSticker
    @State private var appeared = false

    var body: some View {
        Group {
            if sticker.emoji.hasPrefix("sticker_") {
                Image(sticker.emoji)
                    .resizable()
                    .frame(width: 80, height: 80)
            } else {
                Text(sticker.emoji)
                    .font(.system(size: 60))
            }
        }
        .scaleEffect(appeared ? (sticker.celebrating ? 1.3 : 1.0) : 0.1)
        .rotationEffect(.degrees(sticker.rotation +
                                 (sticker.celebrating ? 15 : 0)))
        .position(sticker.position)
        .onAppear {
            withAnimation(.spring(response: 0.35, dampingFraction: 0.45)) {
                appeared = true
            }
        }
    }
}

// MARK: - Sticker Voice (AVSpeechSynthesizer)

class StickerVoice: ObservableObject {
    private let synth = AVSpeechSynthesizer()

    func say(_ text: String) {
        if synth.isSpeaking { synth.stopSpeaking(at: .immediate) }
        let utterance = AVSpeechUtterance(string: text)
        utterance.rate = AVSpeechUtteranceDefaultSpeechRate * 0.85
        utterance.pitchMultiplier = 1.25
        utterance.volume = 0.5
        // Samantha is the warmest built-in en-US voice
        utterance.voice = AVSpeechSynthesisVoice(identifier: "com.apple.voice.compact.en-US.Samantha")
            ?? AVSpeechSynthesisVoice(language: "en-US")
        synth.speak(utterance)
    }
}

// MARK: - Musical Tone Player (Pentatonic — no wrong notes)

class TonePlayer: ObservableObject {
    private let engine = AVAudioEngine()
    private var srcNode: AVAudioSourceNode!
    private var freq: Double = 0
    private var target: Double = 0
    private var amp: Double = 0
    private var phase: Double = 0
    private var sr: Double = 44100

    private let scale: [Double] = [
        261.63, 293.66, 329.63, 392.00, 440.00,
        523.25, 587.33, 659.25, 783.99, 880.00,
    ]

    init() {
        sr = engine.outputNode.outputFormat(forBus: 0).sampleRate
        if sr == 0 { sr = 44100 }

        srcNode = AVAudioSourceNode { [weak self] _, _, frameCount, bufList -> OSStatus in
            guard let self else { return noErr }
            let bufs = UnsafeMutableAudioBufferListPointer(bufList)
            for frame in 0..<Int(frameCount) {
                self.freq += (self.target - self.freq) * 0.005
                let inc = 2 * .pi * self.freq / self.sr
                let val = Float(sin(self.phase) * self.amp)
                self.phase += inc
                if self.phase > 2 * .pi { self.phase -= 2 * .pi }
                for b in bufs {
                    b.mData?.assumingMemoryBound(to: Float.self)[frame] = val
                }
            }
            return noErr
        }

        engine.attach(srcNode)
        let fmt = AVAudioFormat(standardFormatWithSampleRate: sr, channels: 1)!
        engine.connect(srcNode, to: engine.mainMixerNode, format: fmt)

        do {
            try AVAudioSession.sharedInstance().setCategory(.playback)
            try AVAudioSession.sharedInstance().setActive(true)
            try engine.start()
        } catch {
            print("Audio: \(error)")
        }
    }

    func updatePitch(for point: CGPoint, in size: CGSize) {
        let nx = max(0, min(1, Double(point.x / size.width)))
        let idx = min(Int(nx * Double(scale.count)), scale.count - 1)
        target = scale[idx]
        amp = 0.12
    }

    func stop() { amp = 0 }

    private let chirpNotes: [Double] = [
        329.63, 392.00, 440.00, 523.25, 587.33,
        659.25, 783.99, 880.00, 1046.50,
    ]

    func playChirp() {
        target = chirpNotes.randomElement()!
        amp = 0.15
        DispatchQueue.main.asyncAfter(deadline: .now() + Double.random(in: 0.08...0.15)) { [weak self] in
            self?.amp = 0
        }
    }

    func playCelebration() {
        let notes = [523.25, 659.25, 783.99]
        for (i, note) in notes.enumerated() {
            DispatchQueue.main.asyncAfter(deadline: .now() + Double(i) * 0.15) { [weak self] in
                self?.target = note
                self?.amp = 0.2
            }
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.55) { [weak self] in
            self?.amp = 0
        }
    }
}

// MARK: - Background Pattern Views

struct DotGridView: View {
    var body: some View {
        GeometryReader { geo in
            Canvas { ctx, size in
                let spacing: CGFloat = 50
                for x in stride(from: spacing, to: size.width, by: spacing) {
                    for y in stride(from: spacing, to: size.height, by: spacing) {
                        let dot = Path(ellipseIn: CGRect(x: x - 3, y: y - 3,
                                                         width: 6, height: 6))
                        ctx.fill(dot, with: .color(.gray.opacity(0.15)))
                    }
                }
            }
        }
    }
}

struct BigShapesView: View {
    var body: some View {
        GeometryReader { geo in
            let cx = geo.size.width / 2
            let cy = geo.size.height / 2
            ZStack {
                Circle()
                    .stroke(.gray.opacity(0.15), lineWidth: 5)
                    .frame(width: 250, height: 250)
                    .position(x: cx - 120, y: cy - 80)
                RoundedRectangle(cornerRadius: 30)
                    .stroke(.gray.opacity(0.15), lineWidth: 5)
                    .frame(width: 200, height: 200)
                    .position(x: cx + 100, y: cy + 60)
                Capsule()
                    .stroke(.gray.opacity(0.15), lineWidth: 5)
                    .frame(width: 300, height: 120)
                    .position(x: cx, y: cy + 200)
                    .rotationEffect(.degrees(-15))
            }
        }
    }
}

// MARK: - Button Helper

extension View {
    func parentBtn(_ color: Color) -> some View {
        self.font(.headline).foregroundColor(.white)
            .padding(.vertical, 14)
            .background(color).cornerRadius(12)
    }
}
