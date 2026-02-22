import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:image_picker/image_picker.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';
import 'package:flutter_tts/flutter_tts.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'dart:math' as math;
import 'package:url_launcher/url_launcher.dart'; // Güncelleme linkini açmak için
import 'package:firebase_core/firebase_core.dart'; // Yeni eklendi
import 'package:firebase_messaging/firebase_messaging.dart'; // Yeni eklendi
import 'live.dart'; // Canlı mod
import 'sesli.dart'; // Sesli asistan modu
import 'hatabidir.dart';
import 'dart:io'; 

// main.dart dosyanın başındaki main fonksiyonunu bu şekilde güncelle:

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  
  // Firebase Başlatma
  await Firebase.initializeApp();
  
  // Yerel Bildirim Eklentisi Nesnesi
  final FlutterLocalNotificationsPlugin flutterLocalNotificationsPlugin = FlutterLocalNotificationsPlugin();

  // Android için Bildirim Kanalı (Hataların çözüldüğü kısım)
  AndroidNotificationChannel channel = const AndroidNotificationChannel(
    'nova_channel', 
    'Nova Duyuruları',
    description: 'Önemli bildirimler ve güncellemeler',
    importance: Importance.max,
    playSound: true,
  );

  // Kanalı sisteme kaydet
  await flutterLocalNotificationsPlugin
      .resolvePlatformSpecificImplementation<AndroidFlutterLocalNotificationsPlugin>()
      ?.createNotificationChannel(channel);

  // Bildirim İzinlerini Al
  FirebaseMessaging messaging = FirebaseMessaging.instance;
  await messaging.requestPermission(
    alert: true,
    badge: true,
    sound: true,
  );

  // Global Kanal Aboneliği (Admin Panelinden herkes için)
  await messaging.subscribeToTopic("all_users");

  // Token'ı konsola yazdır (Bireysel testler için lazım olabilir)
  String? token = await messaging.getToken();
  debugPrint("Nova Token: $token");

  SystemChrome.setPreferredOrientations([DeviceOrientation.portraitUp])
      .then((_) {
    runApp(const MyApp());
  });
}

class MyApp extends StatefulWidget {
  const MyApp({super.key});
  @override
  State<MyApp> createState() => _MyAppState();
}

class _MyAppState extends State<MyApp> {
  ThemeMode _themeMode = ThemeMode.dark;
  Color _seedColor = const Color(0xFF38BDF8);

  @override
  void initState() {
    super.initState();
    _loadThemeSettings();
  }

  Future<void> _loadThemeSettings() async {
    final prefs = await SharedPreferences.getInstance();
    setState(() {
      int? colorVal = prefs.getInt('user_seed_color');
      if (colorVal != null) _seedColor = Color(colorVal);
    });
  }

  void toggleTheme() => setState(() => _themeMode =
      _themeMode == ThemeMode.light ? ThemeMode.dark : ThemeMode.light);

  void updateSeedColor(Color newColor) async {
    setState(() => _seedColor = newColor);
    final prefs = await SharedPreferences.getInstance();
    await prefs.setInt('user_seed_color', newColor.value);
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      debugShowCheckedModeBanner: false,
      title: 'Nova AI',
      themeMode: _themeMode,
      theme: ThemeData(
        useMaterial3: true,
        brightness: Brightness.light,
        colorSchemeSeed: _seedColor,
        scaffoldBackgroundColor: const Color(0xFFF4F7FA),
        fontFamily: 'Roboto',
      ),
      darkTheme: ThemeData(
        useMaterial3: true,
        brightness: Brightness.dark,
        colorSchemeSeed: _seedColor,
        scaffoldBackgroundColor: const Color(0xFF0F172A),
        fontFamily: 'Roboto',
      ),
      // Uygulama artık Splash Screen ile başlıyor
      home: SplashScreen(
        onThemeToggle: toggleTheme,
        onColorChange: updateSeedColor,
        currentColor: _seedColor,
      ),
    );
  }
}

// --- HAVALI SPLASH SCREEN (AÇILIŞ EKRANI) ---
class SplashScreen extends StatefulWidget {
  final VoidCallback onThemeToggle;
  final Function(Color) onColorChange;
  final Color currentColor;

  const SplashScreen({
    super.key,
    required this.onThemeToggle,
    required this.onColorChange,
    required this.currentColor,
  });

  @override
  State<SplashScreen> createState() => _SplashScreenState();
}

class _SplashScreenState extends State<SplashScreen>
    with TickerProviderStateMixin {
  late AnimationController _mainController;
  late AnimationController _pulseController;

  // --- Animasyon Değerleri ---
  late Animation<Offset> _positionAnim;
  late Animation<double> _rotationAnim;
  late Animation<double> _scaleAnim;
  late Animation<double> _rippleScaleAnim;
  late Animation<double> _rippleOpacityAnim;
  
  // Yeni/Güncellenmiş Animasyonlar
  late Animation<double> _contentFadeOutAnim; // Logo ve metnin kaybolması
  late Animation<double> _finalZoomAnim; // Daha kontrollü büyüme
  late Animation<double> _flashOverlayAnim; // Beyaz ışık patlaması

  @override
  void initState() {
    super.initState();

    // Toplam süre biraz daha kısaldı, daha dinamik olması için.
    _mainController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 2200),
    );

    _pulseController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1500),
    )..repeat(reverse: true);

    const double entranceEnd = 0.5;
    const double exitStart = 0.75;

    // A: GİRİŞ POZİSYONU
    _positionAnim = Tween<Offset>(
      begin: const Offset(-1.5, 1.5),
      end: Offset.zero,
    ).animate(CurvedAnimation(
      parent: _mainController,
      curve: const Interval(0.0, entranceEnd, curve: Curves.easeOutQuart),
    ));

    // B: DÖNÜŞ
    _rotationAnim = Tween<double>(begin: -2.0, end: 0.0).animate(
      CurvedAnimation(
        parent: _mainController,
        curve: const Interval(0.0, entranceEnd + 0.1, curve: Curves.easeOutBack),
      ),
    );

    // C: ELASTİK BÜYÜME
    _scaleAnim = Tween<double>(begin: 0.0, end: 1.0).animate(
      CurvedAnimation(
        parent: _mainController,
        curve: const Interval(0.0, entranceEnd + 0.1, curve: Curves.elasticOut),
      ),
    );

    // D: RIPPLE (Halka)
    _rippleScaleAnim = Tween<double>(begin: 0.5, end: 2.0).animate(
      CurvedAnimation(
        parent: _mainController,
        curve: const Interval(entranceEnd - 0.1, exitStart, curve: Curves.easeOutExpo),
      ),
    );
    _rippleOpacityAnim = Tween<double>(begin: 0.5, end: 0.0).animate(
      CurvedAnimation(
        parent: _mainController,
        curve: const Interval(entranceEnd - 0.1, exitStart, curve: Curves.easeIn),
      ),
    );

    // --- YENİ ÇIKIŞ ANİMASYONLARI ---

    // E: İÇERİK KAYBOLUŞU - Işık gelmeden hemen önce logo netliğini kaybeder
    _contentFadeOutAnim = Tween<double>(begin: 1.0, end: 0.0).animate(
      CurvedAnimation(
        parent: _mainController,
        curve: const Interval(exitStart, 0.9, curve: Curves.easeOut),
      ),
    );

    // F: FİNAL KONTROLLÜ ZOOM - Artık 30x değil, sadece 5x büyüyor.
    // Pikseller bozulmadan hareketi hissettirecek kadar.
    _finalZoomAnim = Tween<double>(begin: 1.0, end: 5.0).animate(
      CurvedAnimation(
        parent: _mainController,
        curve: const Interval(exitStart, 1.0, curve: Curves.easeInExpo),
      ),
    );

    // G: FLASH OVERLAY - Ekranı kaplayan ışık patlaması
    // Son %15'lik dilimde hızla belirir.
    _flashOverlayAnim = Tween<double>(begin: 0.0, end: 1.0).animate(
      CurvedAnimation(
        parent: _mainController,
        curve: const Interval(0.85, 1.0, curve: Curves.easeInCubic),
      ),
    );

    _mainController.forward();

    _mainController.addStatusListener((status) {
      if (status == AnimationStatus.completed) {
        _navigateToHome();
      }
    });
  }

  void _navigateToHome() {
    Navigator.pushReplacement(
      context,
      PageRouteBuilder(
        transitionDuration: const Duration(milliseconds: 800),
        pageBuilder: (_, __, ___) => ChatScreen(
          onThemeToggle: widget.onThemeToggle,
          onColorChange: widget.onColorChange,
          currentColor: widget.currentColor,
        ),
        // FadeTransition kullanarak yumuşak geçiş yapıyoruz.
        // Splash ekranı bembeyaz bitecek, yeni ekran onun içinden belirecek.
        transitionsBuilder: (_, animation, __, child) {
          return FadeTransition(opacity: animation, child: child);
        },
      ),
    );
  }

  @override
  void dispose() {
    _mainController.dispose();
    _pulseController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFF0F172A), // Koyu zemin
      body: AnimatedBuilder(
        animation: _mainController,
        builder: (context, child) {
          // Işık patlamasının rengi: Tema renginin çok açık ve parlak hali
          final Color flashColor = widget.currentColor.withRed(255).withGreen(255).withBlue(255);

          return Stack(
            alignment: Alignment.center,
            children: [
              // 1. KATMAN: ARKA PLAN (Sabit)
               Container(
                decoration: BoxDecoration(
                  gradient: LinearGradient(
                    begin: Alignment.topLeft,
                    end: Alignment.bottomRight,
                    colors: [
                      const Color(0xFF0F172A),
                      widget.currentColor.withOpacity(0.15),
                      Colors.black,
                    ],
                  ),
                ),
              ),

              // 2. KATMAN: ANA İÇERİK (Logo, Metin, Ripple)
              // Çıkış anında (_contentFadeOutAnim) bu katman silikleşecek.
              Opacity(
                opacity: _contentFadeOutAnim.value,
                child: Transform.scale(
                  scale: _finalZoomAnim.value, // Kontrollü büyüme burada uygulanıyor
                  child: Stack(
                     alignment: Alignment.center,
                    children: [
                      // Ripple
                      Opacity(
                        opacity: _rippleOpacityAnim.value,
                        child: Transform.scale(
                          scale: _rippleScaleAnim.value,
                          child: Container(
                            width: 200,
                            height: 200,
                            decoration: BoxDecoration(
                              shape: BoxShape.circle,
                              border: Border.all(
                                color: widget.currentColor.withOpacity(0.5),
                                width: 2,
                              ),
                            ),
                          ),
                        ),
                      ),
                      // Logo ve Metin Grubu
                      _buildLogoAndTextWithEntrance(),
                    ],
                  ),
                ),
              ),

              // 3. KATMAN (EN ÜST): FLASH OVERLAY (Işık Patlaması)
              // En sonda belirip ekranı kaplayacak.
              Opacity(
                opacity: _flashOverlayAnim.value,
                child: Container(
                  width: double.infinity,
                  height: double.infinity,
                  // Saf beyaz yerine, tema renginin "ışık" halini kullanıyoruz.
                  // Bu, gözü daha az yorar ve temaya sadık kalır.
                  color: flashColor, 
                ),
              ),
            ],
          );
        },
      ),
    );
  }

  // Kodu temiz tutmak için logo ve metin girişini ayırdık
  Widget _buildLogoAndTextWithEntrance() {
    return SlideTransition(
      position: _positionAnim,
      child: Transform.rotate(
        angle: _rotationAnim.value * math.pi,
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            ScaleTransition(
              scale: _scaleAnimationWithPulse(),
              child: Container(
                width: 140,
                height: 140,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: Colors.white.withOpacity(0.05),
                  boxShadow: [
                    BoxShadow(
                      color: widget.currentColor.withOpacity(0.5),
                      blurRadius: 30,
                      spreadRadius: 2,
                    )
                  ],
                  border: Border.all(
                      color: widget.currentColor.withOpacity(0.8), width: 1.5),
                ),
                child: ClipOval(
                  child: Image.asset(
                    'lib/images/icon-512.png',
                    fit: BoxFit.cover,
                     // İkon yüklenmezse yedek ikon
                     errorBuilder: (ctx, err, stack) => Icon(
                        Icons.auto_awesome, 
                        size: 80, 
                        color: widget.currentColor
                      ),
                  ),
                ),
              ),
            ),
            const SizedBox(height: 30),
            // Metinler de logo ile birlikte geliyor
            Column(
              children: [
                Text(
                  "NOVA",
                  style: TextStyle(
                    fontSize: 32,
                    fontWeight: FontWeight.w900,
                    color: Colors.white,
                    letterSpacing: 8,
                    shadows: [
                      Shadow(
                        color: widget.currentColor.withOpacity(0.5),
                        blurRadius: 15,
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 5),
                Text(
                  "INTELLIGENCE",
                  style: TextStyle(
                    color: Colors.white60,
                    fontSize: 11,
                    letterSpacing: 3,
                    fontWeight: FontWeight.w300,
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Animation<double> _scaleAnimationWithPulse() {
    if (_scaleAnim.value > 0.9) {
      return Tween<double>(begin: 1.0, end: 1.03).animate(
        CurvedAnimation(parent: _pulseController, curve: Curves.easeInOut),
      );
    }
    return _scaleAnim;
  }
}

// --- ANA SOHBET EKRANI (CHAT SCREEN) ---
class ChatScreen extends StatefulWidget {
  final VoidCallback onThemeToggle;
  final Function(Color) onColorChange;
  final Color currentColor;

  const ChatScreen({
    super.key,
    required this.onThemeToggle,
    required this.onColorChange,
    required this.currentColor,
  });

  @override
  State<ChatScreen> createState() => _ChatScreenState();
}
// Bu bilgileri manuel yönetiyoruz
final String currentVersion = "5.3.0"; // pubspec.yaml'daki ile aynı olmalı
final String storeVersion = "5.3.0";   // Mağazadaki en güncel sürüm (Test için yüksek yazabilirsin)
final String updateUrl = "https://play.google.com/store/apps/details?id=com.novawebb.app";
class _ChatScreenState extends State<ChatScreen> {
  String? _selectedBase64Image;
  File? _selectedImageFile;
Future<void> _pickAndSendImage() async {
    try {
      final ImagePicker picker = ImagePicker();
      final XFile? image = await picker.pickImage(
        source: ImageSource.gallery,
        imageQuality: 50,
      );

      if (image == null) return;

      File imageFile = File(image.path);
      List<int> imageBytes = await imageFile.readAsBytes();
      String base64Image = base64Encode(imageBytes);

      setState(() {
        _selectedImageFile = imageFile;
        _selectedBase64Image = base64Image;
      });
    } catch (e) {
      debugPrint("Görsel seçme hatası: $e");
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text("Görsel seçilemedi!")),
      );
    }
  }
  final TextEditingController _controller = TextEditingController();
  final ScrollController _scrollController = ScrollController();
  final GlobalKey<ScaffoldState> _scaffoldKey = GlobalKey<ScaffoldState>();

  final FlutterTts flutterTts = FlutterTts();
  bool isSpeaking = false;
  String? currentlySpeakingId;

  double ttsSpeed = 0.55;
  double ttsPitch = 1.0;
  String customInstruction = "Sen zeki bir asistansın.";

  List<Map<String, String>> messages = [];
  List<String> chatList = ["Yeni Sohbet"];
  String currentChatName = "Yeni Sohbet";
  bool _isLoading = false;

  @override
  void initState() {
    super.initState();
    _loadAllData();
    _initTts();
    _checkVersionControl();
  }

  String _cleanEmoji(String text) {
    return text
        .replaceAll(
            RegExp(
                r'[\u{1F600}-\u{1F64F}\u{1F300}-\u{1F5FF}\u{1F680}-\u{1F6FF}\u{1F1E6}-\u{1F1FF}\u{2600}-\u{26FF}\u{2700}-\u{27BF}]',
                unicode: true),
            '')
        .trim();
  }

  Future<void> _initTts() async {
    final prefs = await SharedPreferences.getInstance();
    setState(() {
      ttsSpeed = prefs.getDouble('tts_speed') ?? 0.55;
      ttsPitch = prefs.getDouble('tts_pitch') ?? 1.0;
      customInstruction =
          prefs.getString('nova_instr') ?? "Sen zeki bir asistansın.";
    });

    await flutterTts.setLanguage("tr-TR");
    await flutterTts.setPitch(ttsPitch);
    await flutterTts.setSpeechRate(ttsSpeed);

    flutterTts.setCompletionHandler(() => _resetSpeech());
    flutterTts.setCancelHandler(() => _resetSpeech());
    flutterTts.setErrorHandler((m) => _resetSpeech());
  }

  void _resetSpeech() {
    if (mounted) {
      setState(() {
        isSpeaking = false;
        currentlySpeakingId = null;
      });
    }
  }

  Future<void> _handleSpeech(String originalText, String msgId) async {
    await flutterTts.stop();
    if (isSpeaking && currentlySpeakingId == msgId) {
      _resetSpeech();
    } else {
      String cleanText = _cleanEmoji(originalText);
      if (cleanText.isEmpty) return;
      setState(() {
        isSpeaking = true;
        currentlySpeakingId = msgId;
      });
      await flutterTts.speak(cleanText);
    }
  }

  void _showNovaSettings() {
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: Theme.of(context).scaffoldBackgroundColor,
      shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(top: Radius.circular(25))),
      builder: (context) => StatefulBuilder(
        builder: (context, setModalState) => Padding(
          padding: EdgeInsets.only(
              bottom: MediaQuery.of(context).viewInsets.bottom,
              left: 20,
              right: 20,
              top: 20),
          child: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Center(
                    child: Container(
                        width: 40,
                        height: 4,
                        decoration: BoxDecoration(
                            color: Colors.grey[400],
                            borderRadius: BorderRadius.circular(2)))),
                const SizedBox(height: 15),
                const Center(
                    child: Text("NOVA KONTROL MERKEZİ",
                        style: TextStyle(
                            fontSize: 18, fontWeight: FontWeight.bold))),
                const Divider(),
                const Text("Yapay Zeka Talimatı:",
                    style: TextStyle(fontWeight: FontWeight.bold)),
                const SizedBox(height: 8),
                TextField(
                  maxLines: 2,
                  controller: TextEditingController(text: customInstruction),
                  decoration: InputDecoration(
                    hintText: "Örn: Kısa cevap ver, espri yap...",
                    border: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(12)),
                    filled: true,
                  ),
                  onChanged: (val) async {
                    customInstruction = val;
                    (await SharedPreferences.getInstance())
                        .setString('nova_instr', val);
                  },
                ),
                const SizedBox(height: 20),
                Text("Okuma Hızı: ${ttsSpeed.toStringAsFixed(2)}"),
                Slider(
                  value: ttsSpeed,
                  min: 0.1,
                  max: 1.0,
                  activeColor: widget.currentColor,
                  onChanged: (v) async {
                    setModalState(() => ttsSpeed = v);
                    setState(() => ttsSpeed = v);
                    await flutterTts.setSpeechRate(v);
                    (await SharedPreferences.getInstance())
                        .setDouble('tts_speed', v);
                  },
                ),
                Text("Ses Tonu: ${ttsPitch.toStringAsFixed(2)}"),
                Slider(
                  value: ttsPitch,
                  min: 0.5,
                  max: 2.0,
                  activeColor: widget.currentColor,
                  onChanged: (v) async {
                    setModalState(() => ttsPitch = v);
                    setState(() => ttsPitch = v);
                    await flutterTts.setPitch(v);
                    (await SharedPreferences.getInstance())
                        .setDouble('tts_pitch', v);
                  },
                ),
                const SizedBox(height: 10),
                const Text("Tema Rengi:",
                    style: TextStyle(fontWeight: FontWeight.bold)),
                const SizedBox(height: 10),
                Wrap(
                  spacing: 12,
                  runSpacing: 10,
                  children: [
                    const Color(0xFF38BDF8),
                    Colors.purple,
                    Colors.orange,
                    Colors.red,
                    Colors.green,
                    Colors.teal,
                    Colors.indigo
                  ]
                      .map((color) => GestureDetector(
                            onTap: () => widget.onColorChange(color),
                            child: Container(
                              padding: const EdgeInsets.all(2),
                              decoration: BoxDecoration(
                                shape: BoxShape.circle,
                                border: Border.all(
                                    color:
                                        widget.currentColor.value == color.value
                                            ? (Theme.of(context).brightness ==
                                                    Brightness.dark
                                                ? Colors.white
                                                : Colors.black)
                                            : Colors.transparent,
                                    width: 2),
                              ),
                              child: CircleAvatar(
                                backgroundColor: color,
                                radius: 16,
                                child: widget.currentColor.value == color.value
                                    ? const Icon(Icons.check,
                                        color: Colors.white, size: 16)
                                    : null,
                              ),
                            ),
                          ))
                      .toList(),
                ),
                const SizedBox(height: 30),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Future<void> _loadAllData() async {
    final prefs = await SharedPreferences.getInstance();
    setState(() {
      chatList = prefs.getStringList('chat_names') ?? ["Yeni Sohbet"];
      currentChatName = prefs.getString('current_chat_name') ?? "Yeni Sohbet";
    });
    _loadCurrentChatMessages();
  }

  // 1. ZORUNLU GÜNCELLEME KONTROLÜ
  void _checkVersionControl() {
    if (storeVersion != currentVersion) {
      // Sürüm uyuşmuyorsa zorunlu dialog çıkar
      WidgetsBinding.instance.addPostFrameCallback((_) {
        showDialog(
          context: context,
          barrierDismissible: false, // Dışarı tıklayınca kapanmaz
          builder: (context) => WillPopScope(
            onWillPop: () async => false, // Geri tuşuyla kapanmaz
            child: AlertDialog(
              backgroundColor: const Color(0xFF1E293B),
              title: const Text("🚀 Yeni Güncelleme Var!", style: TextStyle(color: Colors.white)),
              content: const Text("Nova AI'nın en yeni özelliklerini kullanabilmek için uygulamayı güncellemeniz gerekiyor. Bu sürüm artık desteklenmiyor.",
                  style: TextStyle(color: Colors.white70)),
              actions: [
                ElevatedButton(
                  style: ElevatedButton.styleFrom(backgroundColor: widget.currentColor),
                  onPressed: () async {
                    final Uri url = Uri.parse(updateUrl);
                    if (await canLaunchUrl(url)) {
                      await launchUrl(url, mode: LaunchMode.externalApplication);
                    }
                  },
                  child: const Text("ŞİMDİ GÜNCELLE", style: TextStyle(color: Colors.white)),
                ),
              ],
            ),
          ),
        );
      });
    } else {
      // Güncelleme yoksa, yenilikleri gösterip göstermeyeceğimizi kontrol et
      _checkWhatsNew();
    }
  }

  // 2. YENİLİKLER SAYFASI (BİR KERELİK)
  void _checkWhatsNew() async {
    final prefs = await SharedPreferences.getInstance();
    // Bu sürüm daha önce görüldü mü?
    bool isSeen = prefs.getBool('seen_version_$currentVersion') ?? false;

    if (!isSeen) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        showModalBottomSheet(
          context: context,
          isScrollControlled: true,
          backgroundColor: const Color(0xFF0F172A),
          shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(25))),
          builder: (context) => Padding(
            padding: const EdgeInsets.all(25.0),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                const Icon(Icons.auto_awesome, color: Colors.amber, size: 50),
                const SizedBox(height: 15),
                Text("Nova Sürüm: $currentVersion", style: const TextStyle(fontSize: 22, fontWeight: FontWeight.bold, color: Colors.white)),
                const Divider(color: Colors.white24, height: 30),
                const Align(
                  alignment: Alignment.centerLeft,
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text("Bu Sürümde Neler Yeni?", style: TextStyle(color: Colors.amber, fontWeight: FontWeight.bold)),
                      SizedBox(height: 10),
                      Text("• Gemini API v1.5 Flash ile yıldırım hızında cevaplar.", style: TextStyle(color: Colors.white70)),
                      Text("• Firebase tabanlı anlık bildirim sistemi.", style: TextStyle(color: Colors.white70)),
                      Text("• Yenilenen ultra hızlı Splash Screen.", style: TextStyle(color: Colors.white70)),
                      Text("• Hata bildirme ve VIP mod geliştirmeleri.", style: TextStyle(color: Colors.white70)),
                    ],
                  ),
                ),
                const SizedBox(height: 30),
                ElevatedButton(
                  style: ElevatedButton.styleFrom(
                    backgroundColor: widget.currentColor,
                    minimumSize: const Size(double.infinity, 50),
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(15))
                  ),
                  onPressed: () async {
                    await prefs.setBool('seen_version_$currentVersion', true);
                    Navigator.pop(context);
                  },
                  child: const Text("ANLADIM, BAŞLAYALIM!", style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold)),
                )
              ],
            ),
          ),
        );
      });
    }
  }

  Future<void> _loadCurrentChatMessages() async {
    final prefs = await SharedPreferences.getInstance();
    String? saved = prefs.getString("chat_data_$currentChatName");
    setState(() {
      messages = saved != null
          ? List<Map<String, String>>.from(
              jsonDecode(saved).map((item) => Map<String, String>.from(item)))
          : [];
    });
    _scrollToBottom();
  }

  Future<void> _saveCurrentChat() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString("chat_data_$currentChatName", jsonEncode(messages));
    await prefs.setStringList('chat_names', chatList);
    await prefs.setString('current_chat_name', currentChatName);
  }

  void _createNewChat() {
    String newName = "Sohbet ${chatList.length + 1}";
    setState(() {
      chatList.add(newName);
      currentChatName = newName;
      messages = [];
    });
    _saveCurrentChat();
    Navigator.pop(context);
  }

  void _deleteChat(String name) {
    setState(() {
      chatList.remove(name);
      if (currentChatName == name) {
        currentChatName = chatList.isNotEmpty ? chatList.last : "Yeni Sohbet";
        if (chatList.isEmpty) chatList.add("Yeni Sohbet");
        _loadCurrentChatMessages();
      }
    });
    _saveCurrentChat();
  }

  void _scrollToBottom() {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (_scrollController.hasClients) {
          _scrollController.animateTo(
            _scrollController.position.maxScrollExtent,
            duration: const Duration(milliseconds: 300),
            curve: Curves.easeOut,
          );
        }
      });
    }
void _sendMessage() async {
    String input = _controller.text.trim();
    if ((input.isEmpty && _selectedBase64Image == null) || _isLoading) return;

    setState(() {
      if (_selectedBase64Image != null) {
        messages.add({
          'sender': 'user', 
          'text': input.isNotEmpty ? "[Görsel] $input" : "[Görsel Gönderildi]"
        });
      } else {
        messages.add({'sender': 'user', 'text': input});
      }
      _isLoading = true;
    });

    String? imageToSend = _selectedBase64Image;
    String messageText = input.isNotEmpty ? input : "Bu görseli analiz et.";

    _controller.clear();
    setState(() {
      _selectedBase64Image = null;
      _selectedImageFile = null;
    });
    _scrollToBottom();

    try {
      final response = await http.post(
        Uri.parse('https://nova-chat-d50f.onrender.com/api/chat'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({
          "userId": "aelif_user",
          "currentChat": currentChatName,
          "message": messageText,
          "image": imageToSend,
          "systemInstruction": customInstruction,
          "history": messages.take(6).toList()
        }),
      );

      if (response.statusCode == 200) {
        String botReply = jsonDecode(utf8.decode(response.bodyBytes))['response'];
        _typeWriter(botReply);
      }
    } catch (e) {
      setState(() {
        messages.add({'sender': 'bot', 'text': 'Bağlantı hatası!'});
        _isLoading = false;
      });
    }
  }

  void _typeWriter(String text) async {
    int idx = messages.length;
    setState(() {
      messages.add({'sender': 'bot', 'text': ''});
      _isLoading = false;
    });
    String current = "";
    for (var char in text.characters) {
      current += char;
      if (mounted) setState(() => messages[idx]['text'] = current);
      _scrollToBottom();
      await Future.delayed(const Duration(milliseconds: 15));
    }
    _saveCurrentChat();
  }

  @override
  Widget build(BuildContext context) {
    bool isDark = Theme.of(context).brightness == Brightness.dark;
    return Scaffold(
      key: _scaffoldKey,
      appBar: AppBar(
        elevation: 0,
        backgroundColor: Colors.transparent,
        leading: IconButton(
            icon: Icon(Icons.menu_open,
                color: isDark ? Colors.white : Colors.black),
            onPressed: () => _scaffoldKey.currentState?.openDrawer()),
        title: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.auto_awesome, color: widget.currentColor, size: 20),
            const SizedBox(width: 8),
            const Text('Nova AI',
                style: TextStyle(fontWeight: FontWeight.bold)),
          ],
        ),
        actions: [
          IconButton(
              icon: Icon(isDark ? Icons.light_mode : Icons.dark_mode),
              onPressed: widget.onThemeToggle),
          IconButton(
              icon: const Icon(Icons.settings), onPressed: _showNovaSettings),
        ],
      ),
      drawer: _buildDrawer(isDark),
      body: Column(
        children: [
          Expanded(
            child: ListView.builder(
              controller: _scrollController,
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
              itemCount: messages.length,
              itemBuilder: (context, index) {
                bool isUser = messages[index]['sender'] == 'user';
                String msgText = messages[index]['text']!;
                bool isTalking =
                    (isSpeaking && currentlySpeakingId == index.toString());
                return _buildMessageBubble(
                    isUser, msgText, index.toString(), isTalking, isDark);
              },
            ),
          ),
          if (_isLoading)
            Padding(
                padding:
                    const EdgeInsets.symmetric(horizontal: 20, vertical: 8),
                child: LinearProgressIndicator(
                    color: widget.currentColor,
                    backgroundColor: Colors.grey[800],
                    borderRadius: BorderRadius.circular(10))),
          _buildInputArea(isDark),
        ],
      ),
    );
  }

  Widget _buildMessageBubble(bool isUser, String originalText, String id,
      bool isTalking, bool isDark) {
    return Align(
      alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
      child: Column(
        crossAxisAlignment:
            isUser ? CrossAxisAlignment.end : CrossAxisAlignment.start,
        children: [
          _processMessageContent(isUser, originalText, id, isTalking, isDark),
          if (!isUser)
            Padding(
              padding: const EdgeInsets.only(left: 4, bottom: 8),
              child: GestureDetector(
                onTap: () => _handleSpeech(originalText, id),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(
                        isTalking
                            ? Icons.stop_circle_outlined
                            : Icons.volume_up_outlined,
                        size: 20,
                        color: Colors.grey),
                    if (isTalking)
                      Padding(
                          padding: const EdgeInsets.only(left: 4),
                          child: Text("Okunuyor...",
                              style: TextStyle(
                                  color: widget.currentColor, fontSize: 12))),
                  ],
                ),
              ),
            ),
        ],
      ),
    );
  }

  Widget _processMessageContent(
      bool isUser, String text, String id, bool isTalking, bool isDark) {
    if (!isUser && text.contains("```")) {
      List<String> parts = text.split("```");
      List<Widget> contentWidgets = [];

      for (int i = 0; i < parts.length; i++) {
        String part = parts[i];
        if (i % 2 != 0) {
          contentWidgets.add(_buildCodeWindow(part));
        } else {
          if (part.trim().isNotEmpty) {
            contentWidgets
                .add(_formattedTextBubble(isUser, part, id, isTalking, isDark));
          }
        }
      }
      return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: contentWidgets);
    }
    return _formattedTextBubble(isUser, text, id, isTalking, isDark);
  }

  Widget _buildCodeWindow(String rawCode) {
    List<String> lines = rawCode.split('\n');
    String firstLine = lines.first.trim().toLowerCase();
    String language = "code";
    String actualCode = rawCode;

    List<String> knownLanguages = [
      "python",
      "dart",
      "javascript",
      "html",
      "css",
      "java",
      "cpp",
      "c#",
      "sql",
      "json"
    ];
    if (knownLanguages.contains(firstLine)) {
      language = firstLine;
      actualCode = lines.sublist(1).join('\n').trim();
    } else {
      actualCode = rawCode.trim();
    }

    return Container(
      margin: const EdgeInsets.symmetric(vertical: 10),
      width: MediaQuery.of(context).size.width * 0.9,
      decoration: BoxDecoration(
        color: const Color(0xFF1E1E1E),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: Colors.white10),
        boxShadow: const [
          BoxShadow(color: Colors.black26, blurRadius: 10, offset: Offset(0, 4))
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text(language.toUpperCase(),
                    style: const TextStyle(
                        color: Colors.grey,
                        fontSize: 11,
                        fontWeight: FontWeight.bold,
                        letterSpacing: 1.1)),
                GestureDetector(
                  onTap: () {
                    Clipboard.setData(ClipboardData(text: actualCode));
                    ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
                        content: Text("Kod kopyalandı!"),
                        duration: Duration(seconds: 1)));
                  },
                  child: const Row(
                    children: [
                      Icon(Icons.copy_rounded, color: Colors.grey, size: 14),
                      SizedBox(width: 4),
                      Text("Kopyala",
                          style: TextStyle(color: Colors.grey, fontSize: 11)),
                    ],
                  ),
                ),
              ],
            ),
          ),
          const Divider(height: 1, color: Colors.white10),
          Padding(
            padding: const EdgeInsets.all(12),
            child: SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: RichText(
                text: TextSpan(
                  style: const TextStyle(
                      fontFamily: 'monospace', fontSize: 13, height: 1.5),
                  children: _highlightSyntax(actualCode),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }

  List<TextSpan> _highlightSyntax(String code) {
    final Map<RegExp, Color> syntaxColors = {
      RegExp(r'\b(if|else|for|while|return|def|class|var|int|double|String|bool|import|void|final|const|print|async|await|try|catch|in|is)\b'):
          const Color(0xFFC586C0),
      RegExp(r'\b\d+\b'): const Color(0xFFB5CEA8),
      RegExp(r'\b\w+(?=\()'): const Color(0xFFDCDCAA),
      RegExp(r'//.*|#.*'): const Color(0xFF6A9955),
    };

    List<TextSpan> spans = [];
    int lastMatchEnd = 0;

    RegExp combinedRegex =
        RegExp(syntaxColors.keys.map((re) => re.pattern).join('|'));

    for (RegExpMatch match in combinedRegex.allMatches(code)) {
      if (match.start > lastMatchEnd) {
        spans.add(TextSpan(
            text: code.substring(lastMatchEnd, match.start),
            style: const TextStyle(color: Color(0xFFD4D4D4))));
      }

      String matchText = match.group(0)!;
      Color matchColor = const Color(0xFFD4D4D4);

      for (var entry in syntaxColors.entries) {
        if (entry.key.hasMatch(matchText)) {
          matchColor = entry.value;
          break;
        }
      }

      spans.add(TextSpan(text: matchText, style: TextStyle(color: matchColor)));
      lastMatchEnd = match.end;
    }

    if (lastMatchEnd < code.length) {
      spans.add(TextSpan(
          text: code.substring(lastMatchEnd),
          style: const TextStyle(color: Color(0xFFD4D4D4))));
    }
    return spans;
  }

  Widget _formattedTextBubble(
      bool isUser, String text, String id, bool isTalking, bool isDark) {
    String textToDisplay = isTalking ? _cleanEmoji(text) : text;
    List<TextSpan> spans = [];
    RegExp exp = RegExp(r"\*\*(.*?)\*\*");
    int lastIndex = 0;

    for (RegExpMatch match in exp.allMatches(textToDisplay)) {
      if (match.start > lastIndex) {
        spans.add(
            TextSpan(text: textToDisplay.substring(lastIndex, match.start)));
      }
      spans.add(TextSpan(
        text: match.group(1),
        style: TextStyle(
          fontWeight: FontWeight.bold,
          backgroundColor: isUser
              ? Colors.black26
              : (isDark ? Colors.white10 : Colors.black12),
          color: isUser
              ? Colors.white
              : (isDark ? widget.currentColor : Colors.black),
        ),
      ));
      lastIndex = match.end;
    }
    if (lastIndex < textToDisplay.length) {
      spans.add(TextSpan(text: textToDisplay.substring(lastIndex)));
    }

    return Container(
      margin: const EdgeInsets.symmetric(vertical: 6),
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      constraints:
          BoxConstraints(maxWidth: MediaQuery.of(context).size.width * 0.85),
      decoration: BoxDecoration(
        color: isUser
            ? widget.currentColor
            : (isDark ? const Color(0xFF1E293B) : Colors.white),
        borderRadius: BorderRadius.only(
          topLeft: const Radius.circular(20),
          topRight: const Radius.circular(20),
          bottomLeft: Radius.circular(isUser ? 20 : 0),
          bottomRight: Radius.circular(isUser ? 0 : 20),
        ),
        boxShadow: [
          BoxShadow(
              color: Colors.black.withOpacity(0.05),
              blurRadius: 6,
              offset: const Offset(0, 2))
        ],
      ),
      child: RichText(
        text: TextSpan(
          style: TextStyle(
              color: isUser
                  ? Colors.white
                  : (isDark ? Colors.white : Colors.black87),
              fontSize: 16,
              height: 1.4),
          children: spans,
        ),
      ),
    );
  }

  Widget _buildDrawer(bool isDark) {
    return Drawer(
      child: Container(
        color: isDark ? const Color(0xFF0F172A) : Colors.white,
        child: Column(
          children: [
            DrawerHeader(
              decoration: BoxDecoration(
                  gradient: LinearGradient(colors: [
                widget.currentColor,
                widget.currentColor.withOpacity(0.5)
              ])),
              child: Center(
                child: Column(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    Container(
                      padding: const EdgeInsets.all(4),
                      decoration: BoxDecoration(
                          shape: BoxShape.circle,
                          border: Border.all(color: Colors.white, width: 2)),
                      child: ClipOval(
                        child: Image.asset(
                          'lib/images/icon-512.png',
                          width: 60,
                          height: 60,
                          fit: BoxFit.cover,
                          errorBuilder: (context, error, stackTrace) =>
                              const Icon(Icons.smart_toy,
                                  color: Colors.white, size: 50),
                        ),
                      ),
                    ),
                    const SizedBox(height: 10),
                    const Text('NOVA INTELLIGENCE',
                        style: TextStyle(
                            color: Colors.white,
                            fontWeight: FontWeight.w900,
                            letterSpacing: 1.2,
                            fontSize: 16)),
                  ],
                ),
              ),
            ),

            // --- VIP, SESLİ ASİSTAN ve HATA BİLDİR ALANI ---
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
              child: Column(
                children: [
                  // VIP BUTONU
                  GestureDetector(
                    onTap: () {
                      Navigator.pop(context);
                      Navigator.push(context,
                          MaterialPageRoute(builder: (context) => NovaLive()));
                    },
                    child: Container(
                      width: double.infinity,
                      padding: const EdgeInsets.symmetric(vertical: 14),
                      decoration: BoxDecoration(
                        gradient: const LinearGradient(
                          colors: [
                            Color(0xFFFFD700),
                            Color(0xFFFFA500),
                            Color(0xFFFF8C00)
                          ],
                          begin: Alignment.topLeft,
                          end: Alignment.bottomRight,
                        ),
                        borderRadius: BorderRadius.circular(15),
                        boxShadow: [
                          BoxShadow(
                            color: Colors.amber.withOpacity(0.5),
                            blurRadius: 12,
                            offset: const Offset(0, 5),
                          ),
                        ],
                      ),
                      child: const Row(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          Icon(Icons.stars_rounded,
                              color: Colors.black, size: 24),
                          SizedBox(width: 10),
                          Text(
                            "LIVE VIP GÖRÜŞME",
                            style: TextStyle(
                              color: Colors.black,
                              fontWeight: FontWeight.w900,
                              fontSize: 15,
                              letterSpacing: 1.2,
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),

                  const SizedBox(height: 12),

                  // SESLİ ASİSTAN
                  ListTile(
                    shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(12)),
                    tileColor: widget.currentColor.withOpacity(0.1),
                    leading: Icon(Icons.mic_rounded,
                        color: widget.currentColor, size: 28),
                    title: Text("SESLİ ASİSTAN",
                        style: TextStyle(
                            fontWeight: FontWeight.bold,
                            color: widget.currentColor)),
                    subtitle: const Text("Konuşarak cevap al"),
                    onTap: () {
                      Navigator.pop(context);
                      Navigator.push(
                        context,
                        MaterialPageRoute(
                          builder: (context) => VoiceAssistantPage(
                            themeColor: widget.currentColor,
                            customInstruction: customInstruction,
                          ),
                        ),
                      );
                    },
                  ),

                  const SizedBox(height: 8),

                  // YENİ: HATA BİLDİR BUTONU (Havalı Kırmızı Tonlar)
                  ListTile(
                    shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(12)),
                    tileColor: Colors.redAccent.withOpacity(0.1),
                    leading: const Icon(Icons.bug_report_rounded,
                        color: Colors.redAccent, size: 28),
                    title: const Text("HATA BİLDİR",
                        style: TextStyle(
                            fontWeight: FontWeight.bold,
                            color: Colors.redAccent)),
                    subtitle: const Text("Geliştirmemize yardım et"),
                    onTap: () {
                      Navigator.pop(context);
                      Navigator.push(
                        context,
                        MaterialPageRoute(
                          builder: (context) => HataBildirPage(
                            themeColor: widget.currentColor,
                          ),
                        ),
                      );
                    },
                  ),
                ],
              ),
            ),

            const Divider(),
            Padding(
              padding: const EdgeInsets.all(16.0),
              child: ElevatedButton.icon(
                style: ElevatedButton.styleFrom(
                    backgroundColor:
                        isDark ? Colors.grey[800] : Colors.grey[200],
                    foregroundColor: isDark ? Colors.white : Colors.black,
                    minimumSize: const Size(double.infinity, 45),
                    shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(15))),
                onPressed: _createNewChat,
                icon: const Icon(Icons.add_comment_rounded),
                label: const Text("Yeni Sohbet"),
              ),
            ),
            Expanded(
                child: ListView.builder(
              padding: EdgeInsets.zero,
              itemCount: chatList.length,
              itemBuilder: (context, i) {
                String name = chatList.reversed.toList()[i];
                bool active = currentChatName == name;
                return ListTile(
                  leading: Icon(Icons.chat_bubble_outline,
                      color: active ? widget.currentColor : Colors.grey,
                      size: 20),
                  title: Text(name,
                      style: TextStyle(
                          fontWeight:
                              active ? FontWeight.bold : FontWeight.normal)),
                  onTap: () {
                    setState(() => currentChatName = name);
                    _loadCurrentChatMessages();
                    Navigator.pop(context);
                  },
                  trailing: IconButton(
                      icon: const Icon(Icons.delete_outline, size: 18),
                      onPressed: () => _deleteChat(name)),
                );
              },
            )),
          ],
        ),
      ),
    );
  }

Widget _buildInputArea(bool isDark) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 8),
      decoration: BoxDecoration(
        color: isDark ? const Color(0xFF1E293B) : Colors.white,
        border: Border(top: BorderSide(color: isDark ? Colors.white10 : Colors.black12)),
      ),
      child: SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            if (_selectedImageFile != null)
              Padding(
                padding: const EdgeInsets.only(bottom: 8.0),
                child: Stack(
                  alignment: Alignment.topRight,
                  children: [
                    Container(
                      height: 80,
                      width: 80,
                      decoration: BoxDecoration(
                        borderRadius: BorderRadius.circular(12),
                        image: DecorationImage(
                          image: FileImage(_selectedImageFile!),
                          fit: BoxFit.cover,
                        ),
                      ),
                    ),
                    GestureDetector(
                      onTap: () => setState(() {
                        _selectedImageFile = null;
                        _selectedBase64Image = null;
                      }),
                      child: Container(
                        decoration: const BoxDecoration(
                          color: Colors.red,
                          shape: BoxShape.circle,
                        ),
                        child: const Icon(Icons.close, color: Colors.white, size: 16),
                      ),
                    ),
                  ],
                ),
              ),
            Row(
              children: [
                IconButton(
                  icon: Icon(Icons.add_a_photo, color: widget.currentColor),
                  onPressed: _pickAndSendImage,
                ),
                Expanded(
                  child: TextField(
                    controller: _controller,
                    style: TextStyle(color: isDark ? Colors.white : Colors.black),
                    decoration: const InputDecoration(
                      hintText: 'Mesaj yaz...',
                      border: InputBorder.none,
                    ),
                  ),
                ),
                IconButton(
                  icon: Icon(Icons.send, color: widget.currentColor),
                  onPressed: _sendMessage,
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}