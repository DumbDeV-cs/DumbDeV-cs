import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math' as math;

import 'package:archive/archive.dart';
import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:geolocator/geolocator.dart';
import 'package:http/http.dart' as http;
import 'package:path_provider/path_provider.dart';

const ink = Color(0xff091426);
const panel = Color(0xff14243a);
const panelLight = Color(0xff1b304b);
const sky = Color(0xff83c8ff);
const mint = Color(0xff8cf0d0);
const muted = Color(0xffa8b8cc);

void main() => runApp(const WeatherRoot());

class WeatherRoot extends StatelessWidget {
  const WeatherRoot({super.key});
  @override
  Widget build(BuildContext context) => MaterialApp(
    title: 'Skyline Weather',
    debugShowCheckedModeBanner: false,
    theme: ThemeData(
      brightness: Brightness.dark,
      scaffoldBackgroundColor: ink,
      colorScheme: ColorScheme.fromSeed(
        seedColor: sky,
        brightness: Brightness.dark,
      ),
      useMaterial3: true,
      fontFamily: 'Roboto',
    ),
    home: const WeatherHome(),
  );
}

class Place {
  const Place(this.name, this.region, this.country, this.lat, this.lon);
  final String name, region, country;
  final double lat, lon;
  String get label => [
    name,
    if (region.isNotEmpty) region,
    if (country.isNotEmpty) country,
  ].join(', ');
  Map<String, dynamic> toJson() => {
    'name': name,
    'region': region,
    'country': country,
    'lat': lat,
    'lon': lon,
  };
  factory Place.fromJson(Map<String, dynamic> j) => Place(
    j['name'] ?? 'Saved location',
    j['region'] ?? '',
    j['country'] ?? '',
    (j['lat'] as num).toDouble(),
    (j['lon'] as num).toDouble(),
  );
}

class WeatherData {
  WeatherData(this.raw);
  final Map<String, dynamic> raw;
  Map<String, dynamic> get current => raw['current'] as Map<String, dynamic>;
  Map<String, dynamic> get hourly => raw['hourly'] as Map<String, dynamic>;
  Map<String, dynamic> get daily => raw['daily'] as Map<String, dynamic>;
  String get condition =>
      conditionName((current['weather_code'] as num).toInt());
  String get kind => conditionKind((current['weather_code'] as num).toInt());
  bool get isDay => (current['is_day'] as num? ?? 1) == 1;
  int get temp => (current['temperature_2m'] as num).round();
  int get feels =>
      (current['apparent_temperature'] as num? ??
              current['temperature_2m'] as num)
          .round();
  int get humidity => (current['relative_humidity_2m'] as num? ?? 0).round();
  int get wind => (current['wind_speed_10m'] as num? ?? 0).round();
  int get uv => ((daily['uv_index_max'] as List?)?.first as num? ?? 0).round();
  int get high => ((daily['temperature_2m_max'] as List).first as num).round();
  int get low => ((daily['temperature_2m_min'] as List).first as num).round();
}

class WeatherApi {
  static Future<List<Place>> search(String query) async {
    final uri = Uri.https('geocoding-api.open-meteo.com', '/v1/search', {
      'name': query,
      'count': '6',
      'language': 'en',
      'format': 'json',
    });
    final response = await http.get(uri).timeout(const Duration(seconds: 8));
    if (response.statusCode != 200) {
      throw Exception('City search is temporarily unavailable.');
    }
    final list = (jsonDecode(response.body)['results'] as List? ?? []);
    return list
        .map(
          (e) => Place(
            e['name'] ?? '',
            e['admin1'] ?? '',
            e['country'] ?? '',
            (e['latitude'] as num).toDouble(),
            (e['longitude'] as num).toDouble(),
          ),
        )
        .toList();
  }

  static Future<WeatherData> forecast(Place place, bool imperial) async {
    final uri = Uri.https('api.open-meteo.com', '/v1/forecast', {
      'latitude': '${place.lat}',
      'longitude': '${place.lon}',
      'timezone': 'auto',
      'temperature_unit': imperial ? 'fahrenheit' : 'celsius',
      'wind_speed_unit': imperial ? 'mph' : 'kmh',
      'current':
          'temperature_2m,relative_humidity_2m,apparent_temperature,is_day,weather_code,wind_speed_10m',
      'hourly': 'temperature_2m,precipitation_probability,weather_code,is_day',
      'daily':
          'weather_code,temperature_2m_max,temperature_2m_min,sunrise,sunset,uv_index_max',
      'forecast_days': '7',
    });
    final response = await http.get(uri).timeout(const Duration(seconds: 10));
    if (response.statusCode != 200) {
      throw Exception(
        'Weather could not load. Check your connection and retry.',
      );
    }
    return WeatherData(jsonDecode(response.body) as Map<String, dynamic>);
  }
}

class WeatherHome extends StatefulWidget {
  const WeatherHome({super.key});
  @override
  State<WeatherHome> createState() => _WeatherHomeState();
}

class _WeatherHomeState extends State<WeatherHome>
    with TickerProviderStateMixin, WidgetsBindingObserver {
  final searchController = TextEditingController();
  final focus = FocusNode();
  final suggestions = <Place>[];
  Timer? debounce;
  Place? place;
  WeatherData? data;
  bool imperial = false, loading = false;
  bool _openingVault = false;
  bool _checkingVaultAttempt = false;
  bool _vaultReady = false;
  String? _vaultCode;
  String? error;
  int requestId = 0;
  late final AnimationController entrance = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 900),
  )..forward();
  late final AnimationController float = AnimationController(
    vsync: this,
    duration: const Duration(seconds: 5),
  )..repeat(reverse: true);

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    focus.addListener(_onFocusChanged);
    _restore();
  }

  void _onFocusChanged() {
    if (mounted) setState(() {});
  }

  @override
  void dispose() {
    debounce?.cancel();
    WidgetsBinding.instance.removeObserver(this);
    searchController.dispose();
    focus.dispose();
    entrance.dispose();
    float.dispose();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      if (!float.isAnimating) float.repeat(reverse: true);
    } else {
      float.stop();
    }
  }

  Future<void> _restore() async {
    const storage = FlutterSecureStorage();
    _vaultCode = await storage.read(key: 'vault_code');
    _vaultReady = true;
    final saved = await storage.read(key: 'last_place');
    if (mounted) setState(() {});
    if (saved == null || !mounted) return;
    try {
      final p = Place.fromJson(jsonDecode(saved));
      await _load(p, save: false);
    } catch (_) {}
  }

  void _onQuery(String value) {
    debounce?.cancel();
    final q = value.trim();
    if (!_vaultReady) {
      setState(() => suggestions.clear());
      return;
    }
    if (_vaultCode != null && q == _vaultCode) {
      _unlockFromSearch(q);
      return;
    }
    // A vault code is private input, not a city query. Avoid sending it to
    // geocoding while the user is typing it.
    if (_vaultCode != null && _vaultCode!.startsWith(q) && q.isNotEmpty) {
      setState(() => suggestions.clear());
      return;
    }
    if (q.length < 2 || !RegExp(r'^[a-zA-Z]').hasMatch(q)) {
      setState(() => suggestions.clear());
      return;
    }
    debounce = Timer(const Duration(milliseconds: 280), () async {
      try {
        final found = await WeatherApi.search(q);
        if (mounted && searchController.text.trim() == q) {
          setState(() {
            suggestions
              ..clear()
              ..addAll(found);
          });
        }
      } catch (_) {
        if (mounted) setState(() => suggestions.clear());
      }
    });
  }

  Future<void> _select(Place p) {
    focus.unfocus();
    setState(() {
      suggestions.clear();
      searchController.text = p.name;
    });
    return _load(p);
  }

  Future<void> _search() async {
    if (_openingVault) return;
    final q = searchController.text.trim();
    if (q.isEmpty) {
      setState(() => error = 'Search for a city to see its weather.');
      return;
    }
    if (!_vaultReady) {
      _vaultCode = await const FlutterSecureStorage().read(key: 'vault_code');
      _vaultReady = true;
      if (!mounted) return;
    }
    if (_vaultCode != null && q == _vaultCode) {
      await _unlockFromSearch(q);
      return;
    }
    // Vault passcodes start with a digit or symbol. Keep those inputs local
    // even when a passcode is mistyped; never send them to geocoding.
    if (!RegExp(r'^[a-zA-Z]').hasMatch(q)) {
      final attemptMessage = await _recordFailedVaultAttempt();
      if (!mounted) return;
      focus.unfocus();
      setState(() {
        suggestions.clear();
        error = attemptMessage ?? 'No city found. Try another search.';
      });
      return;
    }
    focus.unfocus();
    setState(() => suggestions.clear());
    try {
      final results = await WeatherApi.search(q);
      if (results.isEmpty) {
        throw Exception('No matching city. Try adding a region or country.');
      }
      await _load(results.first);
    } catch (e) {
      if (mounted) {
        setState(() {
          loading = false;
          error = _cleanError(e);
        });
      }
    }
  }

  String _cleanError(Object e) => e.toString().replaceFirst('Exception: ', '');

  Future<String?> _recordFailedVaultAttempt() async {
    if (_vaultCode == null) return null;
    if (_checkingVaultAttempt) return 'Please wait a moment.';
    _checkingVaultAttempt = true;
    const storage = FlutterSecureStorage();
    try {
      final now = DateTime.now().millisecondsSinceEpoch;
      final lockUntil =
          int.tryParse(await storage.read(key: 'vault_lockout_until') ?? '') ??
          0;
      if (lockUntil > now) {
        final seconds = ((lockUntil - now) / 1000).ceil();
        return 'Try again in ${seconds}s.';
      }
      final attempts =
          (int.tryParse(
                await storage.read(key: 'vault_failed_attempts') ?? '',
              ) ??
              0) +
          1;
      if (attempts >= 5) {
        final until = now + const Duration(seconds: 30).inMilliseconds;
        await storage.write(key: 'vault_failed_attempts', value: '0');
        await storage.write(
          key: 'vault_lockout_until',
          value: until.toString(),
        );
        return 'Try again in 30s.';
      }
      await storage.write(
        key: 'vault_failed_attempts',
        value: attempts.toString(),
      );
      return 'No city found. Try another search.';
    } catch (_) {
      return 'No city found. Try another search.';
    } finally {
      _checkingVaultAttempt = false;
    }
  }

  Future<void> _unlockFromSearch(String code) async {
    if (_openingVault) return;
    _openingVault = true;
    focus.unfocus();
    debounce?.cancel();
    setState(() {
      suggestions.clear();
      searchController.clear();
      error = null;
    });
    try {
      await _openVault(initialCode: code);
    } finally {
      _openingVault = false;
    }
  }

  bool get _isVaultCodeDraft {
    final query = searchController.text.trim();
    final code = _vaultCode;
    if (!_vaultReady) return true;
    if (query.isEmpty) return false;
    return !RegExp(r'^[a-zA-Z]').hasMatch(query) ||
        (code != null && code.startsWith(query));
  }

  Future<void> _load(Place p, {bool save = true}) async {
    final id = ++requestId;
    setState(() {
      loading = true;
      error = null;
    });
    try {
      final result = await WeatherApi.forecast(p, imperial);
      if (!mounted || id != requestId) return;
      setState(() {
        place = p;
        data = result;
        loading = false;
        error = null;
      });
      entrance.forward(from: 0);
      if (save) {
        const FlutterSecureStorage().write(
          key: 'last_place',
          value: jsonEncode(p.toJson()),
        );
      }
    } catch (e) {
      if (mounted && id == requestId) {
        setState(() {
          loading = false;
          error = _cleanError(e);
        });
      }
    }
  }

  Future<void> _locate() async {
    setState(() {
      loading = true;
      error = null;
    });
    try {
      if (!await Geolocator.isLocationServiceEnabled()) {
        throw Exception('Turn on location services and try again.');
      }
      var permission = await Geolocator.checkPermission();
      if (permission == LocationPermission.denied) {
        permission = await Geolocator.requestPermission();
      }
      if (permission == LocationPermission.denied ||
          permission == LocationPermission.deniedForever) {
        throw Exception('Allow location access to use nearby weather.');
      }
      final pos = await Geolocator.getCurrentPosition(
        locationSettings: const LocationSettings(
          accuracy: LocationAccuracy.medium,
          timeLimit: Duration(seconds: 12),
        ),
      );
      await _load(Place('My location', '', '', pos.latitude, pos.longitude));
    } catch (e) {
      if (mounted) {
        setState(() {
          loading = false;
          error = _cleanError(e);
        });
      }
    }
  }

  void _changeUnits(bool value) {
    setState(() => imperial = value);
    if (place != null) _load(place!);
  }

  String _hourLabel(String iso, int index) {
    if (index == 0) return 'Now';
    final t = DateTime.tryParse(iso);
    if (t == null) return '';
    final h = t.hour;
    return '${h == 0
        ? 12
        : h > 12
        ? h - 12
        : h}${h < 12 ? 'AM' : 'PM'}';
  }

  String _dayLabel(String date, int index) {
    if (index == 0) return 'Today';
    final d = DateTime.tryParse(date);
    return d == null
        ? ''
        : const ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'][d.weekday -
              1];
  }

  @override
  Widget build(BuildContext context) {
    final d = data;
    return Scaffold(
      body: Stack(
        children: [
          const _Backdrop(),
          SafeArea(
            child: CustomScrollView(
              physics: const BouncingScrollPhysics(),
              slivers: [
                SliverPadding(
                  padding: const EdgeInsets.fromLTRB(20, 14, 20, 32),
                  sliver: SliverList(
                    delegate: SliverChildListDelegate([
                      _topBar(),
                      const SizedBox(height: 18),
                      _searchArea(),
                      if (suggestions.isNotEmpty) ...[
                        const SizedBox(height: 8),
                        TweenAnimationBuilder<double>(
                          tween: Tween(begin: 0, end: 1),
                          duration: const Duration(milliseconds: 220),
                          curve: Curves.easeOutCubic,
                          builder: (_, value, child) => Opacity(
                            opacity: value,
                            child: Transform.translate(
                              offset: Offset(0, (1 - value) * 8),
                              child: child,
                            ),
                          ),
                          child: _suggestionPanel(),
                        ),
                      ],
                      if (error != null) ...[
                        const SizedBox(height: 12),
                        _errorBanner(),
                      ],
                      const SizedBox(height: 16),
                      if (d == null)
                        _welcome()
                      else ...[
                        _hero(d),
                        const SizedBox(height: 14),
                        _hourly(d),
                        const SizedBox(height: 14),
                        _details(d),
                        const SizedBox(height: 14),
                        _weekly(d),
                        const SizedBox(height: 16),
                        Center(
                          child: Text(
                            'Forecast by Open-Meteo',
                            style: TextStyle(
                              color: muted.withValues(alpha: .7),
                              fontSize: 11,
                            ),
                          ),
                        ),
                      ],
                    ]),
                  ),
                ),
              ],
            ),
          ),
          if (loading)
            const Positioned(
              top: 0,
              left: 0,
              right: 0,
              child: LinearProgressIndicator(
                minHeight: 2,
                color: sky,
                backgroundColor: Colors.transparent,
              ),
            ),
        ],
      ),
      floatingActionButton: _vaultCode == null
          ? FloatingActionButton.small(
              onPressed: _openVault,
              backgroundColor: panelLight,
              foregroundColor: sky,
              tooltip: 'Set up your private vault',
              child: const Icon(Icons.lock_open_rounded),
            )
          : null,
    );
  }

  Widget _topBar() => Row(
    children: [
      Container(
        width: 42,
        height: 42,
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(14),
          gradient: const LinearGradient(
            colors: [Color(0xff4d93ee), Color(0xff6bdbd2)],
          ),
        ),
        child: const Icon(Icons.wb_twilight_rounded, color: Colors.white),
      ),
      const SizedBox(width: 12),
      const Expanded(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'SKYLINE',
              style: TextStyle(
                fontSize: 12,
                letterSpacing: 2.3,
                fontWeight: FontWeight.w800,
              ),
            ),
            Text(
              'Weather, in focus',
              style: TextStyle(color: muted, fontSize: 11),
            ),
          ],
        ),
      ),
      _unitToggle(),
    ],
  );

  Widget _unitToggle() => Container(
    padding: const EdgeInsets.all(3),
    decoration: BoxDecoration(
      color: panel,
      borderRadius: BorderRadius.circular(16),
      border: Border.all(color: Colors.white.withValues(alpha: .07)),
    ),
    child: Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        _unitButton('°C', !imperial, () => _changeUnits(false)),
        _unitButton('°F', imperial, () => _changeUnits(true)),
      ],
    ),
  );
  Widget _unitButton(String label, bool active, VoidCallback tap) =>
      GestureDetector(
        onTap: tap,
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 180),
          padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 7),
          decoration: BoxDecoration(
            color: active ? const Color(0xff2b4b6c) : Colors.transparent,
            borderRadius: BorderRadius.circular(12),
          ),
          child: Text(
            label,
            style: TextStyle(
              fontWeight: FontWeight.w700,
              fontSize: 12,
              color: active ? Colors.white : muted,
            ),
          ),
        ),
      );

  Widget _searchArea() => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      Row(
        children: [
          Expanded(
            child: AnimatedContainer(
              duration: const Duration(milliseconds: 220),
              height: 52,
              decoration: BoxDecoration(
                color: focus.hasFocus ? const Color(0xff182b44) : panel,
                borderRadius: BorderRadius.circular(18),
                border: Border.all(
                  color: focus.hasFocus
                      ? sky.withValues(alpha: .48)
                      : Colors.white.withValues(alpha: .08),
                ),
                boxShadow: focus.hasFocus
                    ? [
                        BoxShadow(
                          color: sky.withValues(alpha: .08),
                          blurRadius: 18,
                          spreadRadius: 1,
                        ),
                      ]
                    : const [],
              ),
              child: Row(
                children: [
                  const SizedBox(width: 14),
                  const Icon(Icons.search_rounded, color: muted, size: 20),
                  const SizedBox(width: 7),
                  Expanded(
                    child: TextField(
                      controller: searchController,
                      focusNode: focus,
                      onChanged: _onQuery,
                      onSubmitted: (_) => _search(),
                      textInputAction: TextInputAction.search,
                      obscureText: _isVaultCodeDraft,
                      enableSuggestions: !_isVaultCodeDraft,
                      autocorrect: false,
                      keyboardType: _isVaultCodeDraft
                          ? TextInputType.visiblePassword
                          : TextInputType.text,
                      decoration: const InputDecoration(
                        hintText: 'Search city or region',
                        hintStyle: TextStyle(
                          color: Color(0xff8294aa),
                          fontSize: 14,
                        ),
                        border: InputBorder.none,
                        isDense: true,
                      ),
                    ),
                  ),
                  if (searchController.text.isNotEmpty)
                    IconButton(
                      onPressed: () {
                        searchController.clear();
                        setState(() => suggestions.clear());
                      },
                      icon: const Icon(
                        Icons.close_rounded,
                        size: 18,
                        color: muted,
                      ),
                    ),
                ],
              ),
            ),
          ),
          const SizedBox(width: 9),
          _iconAction(Icons.near_me_rounded, 'Use my location', _locate),
          const SizedBox(width: 8),
          SizedBox(
            height: 52,
            child: FilledButton(
              onPressed: _search,
              style: FilledButton.styleFrom(
                backgroundColor: const Color(0xff3c7dcc),
                shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(17),
                ),
                padding: const EdgeInsets.symmetric(horizontal: 17),
              ),
              child: const Text(
                'Go',
                style: TextStyle(fontWeight: FontWeight.w700),
              ),
            ),
          ),
        ],
      ),
      if (place != null && suggestions.isEmpty)
        Padding(
          padding: const EdgeInsets.only(left: 4, top: 10),
          child: Row(
            children: [
              const Icon(Icons.place_rounded, color: mint, size: 14),
              const SizedBox(width: 5),
              Expanded(
                child: Text(
                  place!.label,
                  style: const TextStyle(color: muted, fontSize: 12),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ],
          ),
        ),
    ],
  );
  Widget _iconAction(IconData icon, String label, VoidCallback tap) => Tooltip(
    message: label,
    child: SizedBox(
      width: 50,
      height: 52,
      child: Material(
        color: panelLight,
        borderRadius: BorderRadius.circular(17),
        child: InkWell(
          borderRadius: BorderRadius.circular(17),
          onTap: tap,
          child: Icon(icon, color: mint, size: 20),
        ),
      ),
    ),
  );

  Widget _suggestionPanel() => Container(
    decoration: BoxDecoration(
      color: const Color(0xff15263c),
      borderRadius: BorderRadius.circular(17),
      border: Border.all(color: Colors.white.withValues(alpha: .08)),
    ),
    child: Column(
      children: [
        for (var i = 0; i < suggestions.length; i++)
          Column(
            children: [
              InkWell(
                onTap: () => _select(suggestions[i]),
                child: Padding(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 14,
                    vertical: 11,
                  ),
                  child: Row(
                    children: [
                      Icon(
                        i == 0
                            ? Icons.location_on_rounded
                            : Icons.location_city_rounded,
                        color: i == 0 ? mint : sky,
                        size: 18,
                      ),
                      const SizedBox(width: 10),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              suggestions[i].name,
                              style: const TextStyle(
                                fontWeight: FontWeight.w700,
                              ),
                            ),
                            Text(
                              [
                                suggestions[i].region,
                                suggestions[i].country,
                              ].where((x) => x.isNotEmpty).join(', '),
                              style: const TextStyle(
                                color: muted,
                                fontSize: 11,
                              ),
                            ),
                          ],
                        ),
                      ),
                      const Icon(
                        Icons.north_west_rounded,
                        size: 15,
                        color: muted,
                      ),
                    ],
                  ),
                ),
              ),
              if (i != suggestions.length - 1)
                Divider(
                  height: 1,
                  indent: 42,
                  color: Colors.white.withValues(alpha: .06),
                ),
            ],
          ),
      ],
    ),
  );

  Widget _errorBanner() => Container(
    padding: const EdgeInsets.all(12),
    decoration: BoxDecoration(
      color: const Color(0xff472b37),
      borderRadius: BorderRadius.circular(14),
    ),
    child: Row(
      children: [
        const Icon(
          Icons.info_outline_rounded,
          color: Color(0xffffb8a4),
          size: 18,
        ),
        const SizedBox(width: 9),
        Expanded(
          child: Text(
            error!,
            style: const TextStyle(color: Color(0xffffd5c9), fontSize: 12),
          ),
        ),
        IconButton(
          onPressed: () => place == null ? _search() : _load(place!),
          icon: const Icon(Icons.refresh_rounded, size: 18),
          visualDensity: VisualDensity.compact,
        ),
      ],
    ),
  );

  Widget _welcome() => FadeTransition(
    opacity: entrance,
    child: Container(
      height: 420,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(30),
        gradient: const LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [Color(0xff15314d), Color(0xff101c31)],
        ),
        border: Border.all(color: Colors.white.withValues(alpha: .08)),
      ),
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          SizedBox(
            height: 180,
            child: AnimatedBuilder(
              animation: float,
              builder: (_, _) => RepaintBoundary(
                child: CustomPaint(
                  painter: WeatherPainter('partly', true, float.value),
                  size: const Size(220, 180),
                ),
              ),
            ),
          ),
          const Text(
            'A little more sky.',
            style: TextStyle(fontWeight: FontWeight.w700, fontSize: 23),
          ),
          const SizedBox(height: 8),
          const Text(
            'Search a city to see the forecast\nfor the hours and days ahead.',
            textAlign: TextAlign.center,
            style: TextStyle(color: muted, height: 1.55),
          ),
          const SizedBox(height: 22),
          const Icon(Icons.keyboard_arrow_up_rounded, color: sky),
        ],
      ),
    ),
  );

  Widget _hero(WeatherData d) => FadeTransition(
    opacity: entrance,
    child: Container(
      clipBehavior: Clip.antiAlias,
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(28),
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: d.isDay
              ? const [Color(0xff1c4264), Color(0xff162841), Color(0xff14243a)]
              : const [Color(0xff1c2b52), Color(0xff16213b), Color(0xff121c31)],
        ),
        border: Border.all(color: Colors.white.withValues(alpha: .1)),
        boxShadow: [
          BoxShadow(
            color: sky.withValues(alpha: .08),
            blurRadius: 35,
            offset: const Offset(0, 16),
          ),
        ],
      ),
      child: Stack(
        children: [
          Positioned(
            right: -38,
            top: -35,
            child: Container(
              width: 190,
              height: 190,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: sky.withValues(alpha: .04),
              ),
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(20, 18, 20, 17),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    const Icon(Icons.place_rounded, color: mint, size: 16),
                    const SizedBox(width: 5),
                    Expanded(
                      child: Text(
                        place?.name ?? 'Weather',
                        style: const TextStyle(
                          fontSize: 17,
                          fontWeight: FontWeight.w700,
                        ),
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                    Text(
                      '${d.high}° / ${d.low}°',
                      style: const TextStyle(color: muted, fontSize: 12),
                    ),
                  ],
                ),
                Text(
                  [
                    place?.region,
                    place?.country,
                  ].whereType<String>().where((e) => e.isNotEmpty).join(' · '),
                  style: const TextStyle(color: muted, fontSize: 11),
                ),
                const SizedBox(height: 5),
                SizedBox(
                  height: 174,
                  child: Row(
                    children: [
                      Expanded(
                        flex: 5,
                        child: AnimatedBuilder(
                          animation: float,
                          builder: (_, _) => RepaintBoundary(
                            child: CustomPaint(
                              painter: WeatherPainter(
                                d.kind,
                                d.isDay,
                                float.value,
                              ),
                              size: const Size(190, 174),
                            ),
                          ),
                        ),
                      ),
                      Expanded(
                        flex: 4,
                        child: Column(
                          mainAxisAlignment: MainAxisAlignment.center,
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            AnimatedSwitcher(
                              duration: const Duration(milliseconds: 450),
                              transitionBuilder: (child, a) => FadeTransition(
                                opacity: a,
                                child: SlideTransition(
                                  position: Tween(
                                    begin: const Offset(0, .12),
                                    end: Offset.zero,
                                  ).animate(a),
                                  child: child,
                                ),
                              ),
                              child: Text(
                                '${d.temp}°',
                                key: ValueKey(d.temp),
                                style: const TextStyle(
                                  fontSize: 63,
                                  fontWeight: FontWeight.w300,
                                  letterSpacing: -4,
                                  height: 1,
                                ),
                              ),
                            ),
                            Text(
                              d.condition,
                              style: const TextStyle(
                                fontSize: 15,
                                fontWeight: FontWeight.w600,
                              ),
                            ),
                            const SizedBox(height: 4),
                            Text(
                              'Feels like ${d.feels}°',
                              style: const TextStyle(
                                color: muted,
                                fontSize: 12,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                ),
                const Divider(color: Color(0x22ffffff), height: 14),
                Row(
                  children: [
                    const Icon(
                      Icons.wb_sunny_outlined,
                      color: Color(0xffffd27a),
                      size: 16,
                    ),
                    const SizedBox(width: 6),
                    Expanded(
                      child: Text(
                        'H ${d.high}°  ·  L ${d.low}°',
                        style: const TextStyle(color: muted, fontSize: 11),
                      ),
                    ),
                    Text(
                      'Updated ${_time(d.current['time'] as String?)}',
                      style: const TextStyle(color: muted, fontSize: 10),
                    ),
                  ],
                ),
              ],
            ),
          ),
        ],
      ),
    ),
  );

  String _time(String? iso) {
    final t = iso == null ? null : DateTime.tryParse(iso);
    if (t == null) return 'now';
    final h = t.hour % 12 == 0 ? 12 : t.hour % 12;
    return '$h:${t.minute.toString().padLeft(2, '0')} ${t.hour < 12 ? 'AM' : 'PM'}';
  }

  Widget _sectionCard({
    required String title,
    required Widget child,
    Widget? trailing,
  }) => Container(
    padding: const EdgeInsets.fromLTRB(16, 15, 16, 14),
    decoration: BoxDecoration(
      color: panel.withValues(alpha: .93),
      borderRadius: BorderRadius.circular(23),
      border: Border.all(color: Colors.white.withValues(alpha: .065)),
    ),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Expanded(
              child: Text(
                title.toUpperCase(),
                style: const TextStyle(
                  fontSize: 10,
                  fontWeight: FontWeight.w800,
                  letterSpacing: 1.3,
                  color: muted,
                ),
              ),
            ),
            ?trailing,
          ],
        ),
        const SizedBox(height: 12),
        child,
      ],
    ),
  );

  Widget _hourly(WeatherData d) {
    final times = d.hourly['time'] as List;
    final temps = d.hourly['temperature_2m'] as List;
    final rains = d.hourly['precipitation_probability'] as List;
    final codes = d.hourly['weather_code'] as List;
    final now = DateTime.tryParse(d.current['time'] as String? ?? '');
    var start = 0;
    if (now != null) {
      final idx = times.indexWhere(
        (v) =>
            DateTime.tryParse(
              v,
            )?.isAfter(now.subtract(const Duration(hours: 1))) ??
            false,
      );
      if (idx >= 0) start = idx;
    }
    final count = math.min(12, times.length - start);
    final values = List<double>.generate(
      count,
      (i) => (temps[start + i] as num).toDouble(),
    );
    return _sectionCard(
      title: 'Next hours',
      trailing: const Text(
        'TEMP · RAIN',
        style: TextStyle(color: muted, fontSize: 9, letterSpacing: .5),
      ),
      child: Column(
        children: [
          SizedBox(
            height: 68,
            child: RepaintBoundary(
              child: CustomPaint(
                painter: TrendPainter(values, float.value),
                child: const SizedBox.expand(),
              ),
            ),
          ),
          const SizedBox(height: 4),
          SizedBox(
            height: 86,
            child: ListView.separated(
              scrollDirection: Axis.horizontal,
              physics: const BouncingScrollPhysics(),
              itemCount: count,
              separatorBuilder: (_, _) => const SizedBox(width: 12),
              itemBuilder: (_, i) {
                final k = start + i;
                final kind = conditionKind((codes[k] as num).toInt());
                final rain = rains[k] as num?;
                return SizedBox(
                  width: 43,
                  child: Column(
                    children: [
                      Text(
                        _hourLabel(times[k] as String, i),
                        style: const TextStyle(color: muted, fontSize: 9),
                      ),
                      const SizedBox(height: 5),
                      Icon(weatherIcon(kind), color: sky, size: 19),
                      const SizedBox(height: 4),
                      Text(
                        '${(temps[k] as num).round()}°',
                        style: const TextStyle(
                          fontSize: 12,
                          fontWeight: FontWeight.w700,
                        ),
                      ),
                      const SizedBox(height: 4),
                      Text(
                        rain == null ? '–' : '${rain.round()}%',
                        style: TextStyle(
                          color: (rain ?? 0) >= 30 ? sky : muted,
                          fontSize: 9,
                        ),
                      ),
                    ],
                  ),
                );
              },
            ),
          ),
        ],
      ),
    );
  }

  Widget _details(WeatherData d) => Row(
    children: [
      _metric(
        'FEELS LIKE',
        '${d.feels}°',
        Icons.thermostat_rounded,
        const Color(0xffffbd82),
      ),
      const SizedBox(width: 8),
      _metric('HUMIDITY', '${d.humidity}%', Icons.water_drop_rounded, sky),
      const SizedBox(width: 8),
      _metric(
        'WIND',
        '${d.wind} ${imperial ? 'mph' : 'km/h'}',
        Icons.air_rounded,
        mint,
      ),
      const SizedBox(width: 8),
      _metric(
        'UV INDEX',
        '${d.uv}',
        Icons.wb_sunny_rounded,
        const Color(0xffffd27a),
      ),
    ],
  );
  Widget _metric(String title, String value, IconData icon, Color color) =>
      Expanded(
        child: Container(
          height: 96,
          padding: const EdgeInsets.fromLTRB(9, 11, 5, 8),
          decoration: BoxDecoration(
            color: panel,
            borderRadius: BorderRadius.circular(18),
            border: Border.all(color: Colors.white.withValues(alpha: .06)),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Icon(icon, color: color, size: 16),
              const Spacer(),
              FittedBox(
                fit: BoxFit.scaleDown,
                child: Text(
                  value,
                  style: const TextStyle(
                    fontSize: 16,
                    fontWeight: FontWeight.w700,
                  ),
                ),
              ),
              const SizedBox(height: 1),
              FittedBox(
                fit: BoxFit.scaleDown,
                child: Text(
                  title,
                  style: const TextStyle(
                    color: muted,
                    fontSize: 8,
                    letterSpacing: .4,
                  ),
                ),
              ),
            ],
          ),
        ),
      );

  Widget _weekly(WeatherData d) {
    final dates = d.daily['time'] as List,
        codes = d.daily['weather_code'] as List,
        lows = d.daily['temperature_2m_min'] as List,
        highs = d.daily['temperature_2m_max'] as List;
    final min = (lows.cast<num>().reduce(math.min)).toDouble(),
        max = (highs.cast<num>().reduce(math.max)).toDouble(),
        span = math.max(1, max - min).toDouble();
    return _sectionCard(
      title: '7-day outlook',
      child: Column(
        children: [
          for (var i = 0; i < dates.length; i++)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 5),
              child: Row(
                children: [
                  SizedBox(
                    width: 48,
                    child: Text(
                      _dayLabel(dates[i] as String, i),
                      style: TextStyle(
                        color: i == 0 ? Colors.white : muted,
                        fontSize: 11,
                        fontWeight: i == 0 ? FontWeight.w700 : FontWeight.w500,
                      ),
                    ),
                  ),
                  Icon(
                    weatherIcon(conditionKind((codes[i] as num).toInt())),
                    size: 17,
                    color: sky,
                  ),
                  const SizedBox(width: 10),
                  SizedBox(
                    width: 30,
                    child: Text(
                      '${(lows[i] as num).round()}°',
                      textAlign: TextAlign.right,
                      style: const TextStyle(color: muted, fontSize: 11),
                    ),
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: _RangeBar(
                      low: (lows[i] as num).toDouble(),
                      high: (highs[i] as num).toDouble(),
                      min: min,
                      span: span,
                    ),
                  ),
                  const SizedBox(width: 8),
                  SizedBox(
                    width: 30,
                    child: Text(
                      '${(highs[i] as num).round()}°',
                      style: const TextStyle(
                        fontSize: 11,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                  ),
                ],
              ),
            ),
        ],
      ),
    );
  }

  Future<void> _openVault({String? initialCode}) async {
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => VaultSheet(initialCode: initialCode),
    );
    final code = await const FlutterSecureStorage().read(key: 'vault_code');
    if (mounted && code != _vaultCode) setState(() => _vaultCode = code);
  }
}

class _Backdrop extends StatelessWidget {
  const _Backdrop();
  @override
  Widget build(BuildContext context) => Stack(
    fit: StackFit.expand,
    children: [
      const DecoratedBox(
        decoration: BoxDecoration(
          gradient: LinearGradient(
            begin: Alignment.topCenter,
            end: Alignment.bottomCenter,
            colors: [Color(0xff10213a), ink, Color(0xff0a1424)],
          ),
        ),
      ),
      Positioned(
        top: -150,
        right: -105,
        child: _ambientGlow(350, const [Color(0x264b91ec), Color(0x004b91ec)]),
      ),
      Positioned(
        bottom: 70,
        left: -190,
        child: _ambientGlow(330, const [Color(0x1d36bcae), Color(0x0036bcae)]),
      ),
      Positioned.fill(
        child: IgnorePointer(child: CustomPaint(painter: _AtmospherePainter())),
      ),
    ],
  );

  Widget _ambientGlow(double size, List<Color> colors) => Container(
    width: size,
    height: size,
    decoration: BoxDecoration(
      shape: BoxShape.circle,
      gradient: RadialGradient(colors: colors),
    ),
  );
}

class _AtmospherePainter extends CustomPainter {
  @override
  void paint(Canvas canvas, Size size) {
    final points = <Offset>[
      Offset(size.width * .14, size.height * .17),
      Offset(size.width * .82, size.height * .27),
      Offset(size.width * .73, size.height * .69),
      Offset(size.width * .22, size.height * .82),
      Offset(size.width * .91, size.height * .9),
    ];
    for (var i = 0; i < points.length; i++) {
      final radius = i.isEven ? 1.4 : 1.0;
      canvas.drawCircle(
        points[i],
        radius,
        Paint()..color = Colors.white.withValues(alpha: .13),
      );
    }
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
}

class _RangeBar extends StatelessWidget {
  const _RangeBar({
    required this.low,
    required this.high,
    required this.min,
    required this.span,
  });
  final double low, high, min, span;
  @override
  Widget build(BuildContext context) => SizedBox(
    height: 21,
    child: CustomPaint(
      painter: _RangePainter((low - min) / span, (high - min) / span),
    ),
  );
}

class _RangePainter extends CustomPainter {
  _RangePainter(this.a, this.b);
  final double a, b;
  @override
  void paint(Canvas canvas, Size size) {
    final y = size.height / 2;
    final bg = Paint()
      ..color = const Color(0xff33465b)
      ..strokeWidth = 4
      ..strokeCap = StrokeCap.round;
    canvas.drawLine(Offset(0, y), Offset(size.width, y), bg);
    final fg = Paint()
      ..shader = const LinearGradient(
        colors: [Color(0xff81d5d1), Color(0xffffc56e)],
      ).createShader(Offset.zero & size)
      ..strokeWidth = 4
      ..strokeCap = StrokeCap.round;
    canvas.drawLine(Offset(size.width * a, y), Offset(size.width * b, y), fg);
  }

  @override
  bool shouldRepaint(covariant _RangePainter old) => old.a != a || old.b != b;
}

class TrendPainter extends CustomPainter {
  TrendPainter(this.values, this.phase);
  final List<double> values;
  final double phase;
  @override
  void paint(Canvas canvas, Size size) {
    if (values.length < 2 || size.width <= 0) return;
    final lo = values.reduce((a, b) => a < b ? a : b),
        hi = values.reduce((a, b) => a > b ? a : b),
        span = math.max(1.0, hi - lo);
    final pts = List<Offset>.generate(
      values.length,
      (i) => Offset(
        5 + i * (size.width - 10) / (values.length - 1),
        size.height - 9 - ((values[i] - lo) / span) * (size.height - 23),
      ),
    );
    final path = Path()
      ..moveTo(pts.first.dx, size.height)
      ..lineTo(pts.first.dx, pts.first.dy);
    for (var i = 1; i < pts.length; i++) {
      path.cubicTo(
        (pts[i - 1].dx + pts[i].dx) / 2,
        pts[i - 1].dy,
        (pts[i - 1].dx + pts[i].dx) / 2,
        pts[i].dy,
        pts[i].dx,
        pts[i].dy,
      );
    }
    path.lineTo(pts.last.dx, size.height);
    path.close();
    canvas.drawPath(
      path,
      Paint()
        ..shader = const LinearGradient(
          begin: Alignment.topCenter,
          end: Alignment.bottomCenter,
          colors: [Color(0x4478bdff), Color(0x0078bdff)],
        ).createShader(Offset.zero & size),
    );
    final line = Path()..moveTo(pts.first.dx, pts.first.dy);
    for (var i = 1; i < pts.length; i++) {
      line.cubicTo(
        (pts[i - 1].dx + pts[i].dx) / 2,
        pts[i - 1].dy,
        (pts[i - 1].dx + pts[i].dx) / 2,
        pts[i].dy,
        pts[i].dx,
        pts[i].dy,
      );
    }
    canvas.drawPath(
      line,
      Paint()
        ..color = const Color(0xff80c9ff)
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2.3
        ..strokeCap = StrokeCap.round,
    );
    final cursor =
        pts[(phase * (pts.length - 1)).round().clamp(0, pts.length - 1)];
    canvas.drawCircle(cursor, 8, Paint()..color = sky.withValues(alpha: .16));
    canvas.drawCircle(cursor, 3.5, Paint()..color = Colors.white);
  }

  @override
  bool shouldRepaint(covariant TrendPainter old) =>
      old.values != values || old.phase != phase;
}

class WeatherPainter extends CustomPainter {
  WeatherPainter(this.kind, this.day, this.phase);
  final String kind;
  final bool day;
  final double phase;
  @override
  void paint(Canvas canvas, Size size) {
    final s = math.min(size.width, size.height) / 170,
        cx = size.width * .51,
        cy = size.height * .52 + math.sin(phase * math.pi) * 3 * s;
    final star = Paint()..color = Colors.white.withValues(alpha: .6);
    if (!day) {
      for (var i = 0; i < 10; i++) {
        final x = (i * 47.0 + 9) % size.width,
            y = (i * 29.0 + 15) % size.height;
        canvas.drawCircle(
          Offset(x, y),
          (i % 3 + 1) * .65 * s,
          star
            ..color = Colors.white.withValues(
              alpha: .25 + .25 * math.sin(phase * 2 + i).abs(),
            ),
        );
      }
    }
    if (kind == 'clear' && day || kind == 'partly' && day) {
      final radius = (25 + 1.5 * math.sin(phase * math.pi * 2)) * s;
      canvas.drawCircle(
        Offset(cx - 16 * s, cy - 19 * s),
        radius + 14 * s,
        Paint()..color = const Color(0xffffcf75).withValues(alpha: .07),
      );
      final glow = Paint()
        ..shader =
            RadialGradient(
              colors: [
                const Color(0xffffe7a0),
                const Color(0xffffbb4b),
                const Color(0xffffa13d).withValues(alpha: .1),
              ],
            ).createShader(
              Rect.fromCircle(
                center: Offset(cx - 16 * s, cy - 19 * s),
                radius: radius,
              ),
            );
      canvas.drawCircle(Offset(cx - 16 * s, cy - 19 * s), radius, glow);
      for (var i = 0; i < 12; i++) {
        final a = i * math.pi / 6 + phase * .12;
        canvas.drawLine(
          Offset(
            cx - 16 * s + math.cos(a) * 32 * s,
            cy - 19 * s + math.sin(a) * 32 * s,
          ),
          Offset(
            cx - 16 * s + math.cos(a) * 39 * s,
            cy - 19 * s + math.sin(a) * 39 * s,
          ),
          Paint()
            ..color = const Color(0xffffd681)
            ..strokeWidth = 2 * s
            ..strokeCap = StrokeCap.round,
        );
      }
      if (kind == 'clear') return;
    }
    final cloudCenter = Offset(
      cx + (kind == 'partly' ? 11 : 0) * s,
      cy + 15 * s,
    );
    final cloud = Paint()
      ..shader =
          const LinearGradient(
            begin: Alignment.topCenter,
            end: Alignment.bottomCenter,
            colors: [Color(0xfff6fbff), Color(0xff9db8d2)],
          ).createShader(
            Rect.fromCenter(
              center: cloudCenter,
              width: 100 * s,
              height: 54 * s,
            ),
          );
    final cloudShade = Paint()
      ..color = const Color(0xff526f91).withValues(alpha: .42);
    canvas.drawCircle(
      cloudCenter.translate(-20 * s, 2 * s),
      18 * s,
      cloudShade,
    );
    canvas.drawCircle(cloudCenter.translate(2 * s, -9 * s), 25 * s, cloudShade);
    canvas.drawCircle(cloudCenter.translate(25 * s, 2 * s), 17 * s, cloudShade);
    canvas.drawRRect(
      RRect.fromRectAndRadius(
        Rect.fromCenter(
          center: cloudCenter.translate(3 * s, 12 * s),
          width: 67 * s,
          height: 26 * s,
        ),
        Radius.circular(12 * s),
      ),
      cloudShade,
    );
    canvas.drawCircle(cloudCenter.translate(-20 * s, -2 * s), 18 * s, cloud);
    canvas.drawCircle(cloudCenter.translate(2 * s, -13 * s), 25 * s, cloud);
    canvas.drawCircle(cloudCenter.translate(25 * s, -2 * s), 17 * s, cloud);
    canvas.drawRRect(
      RRect.fromRectAndRadius(
        Rect.fromCenter(
          center: cloudCenter.translate(3 * s, 8 * s),
          width: 67 * s,
          height: 26 * s,
        ),
        Radius.circular(12 * s),
      ),
      cloud,
    );
    if (kind == 'rain' || kind == 'storm') {
      for (var i = 0; i < 5; i++) {
        final x = cx - 25 * s + i * 12 * s + math.sin(phase * 6 + i) * 2 * s,
            y = cy + 29 * s + ((phase * 32 + i * 11) % 35) * s;
        canvas.drawLine(
          Offset(x, y),
          Offset(x - 4 * s, y + 8 * s),
          Paint()
            ..color = sky.withValues(alpha: .8)
            ..strokeWidth = 2 * s
            ..strokeCap = StrokeCap.round,
        );
      }
    }
    if (kind == 'snow') {
      for (var i = 0; i < 6; i++) {
        final x = cx - 25 * s + i * 10 * s + math.sin(phase * 2 + i) * 3 * s,
            y = cy + 32 * s + ((phase * 12 + i * 8) % 34) * s;
        canvas.drawCircle(
          Offset(x, y),
          2 * s,
          Paint()..color = Colors.white.withValues(alpha: .8),
        );
      }
      if (kind == 'storm' && phase > .84) {
        final lightning = Path()
          ..moveTo(cx + 9 * s, cy + 19 * s)
          ..lineTo(cx - 2 * s, cy + 38 * s)
          ..lineTo(cx + 7 * s, cy + 38 * s)
          ..lineTo(cx - 3 * s, cy + 54 * s);
        canvas.drawPath(
          lightning,
          Paint()
            ..color = const Color(0xffffd56a)
            ..style = PaintingStyle.stroke
            ..strokeWidth = 3 * s
            ..strokeCap = StrokeCap.round,
        );
      }
    }
    if (kind == 'fog') {
      for (var i = 0; i < 3; i++) {
        final y = cy + 14 * s + i * 10 * s + math.sin(phase * 4 + i) * 2 * s;
        canvas.drawLine(
          Offset(cx - 38 * s, y),
          Offset(cx + 38 * s, y),
          Paint()
            ..color = Colors.white.withValues(alpha: .6)
            ..strokeWidth = 3 * s
            ..strokeCap = StrokeCap.round,
        );
      }
    }
  }

  @override
  bool shouldRepaint(covariant WeatherPainter old) =>
      old.kind != kind || old.day != day || old.phase != phase;
}

String conditionName(int code) => switch (code) {
  0 => 'Clear sky',
  1 => 'Mainly clear',
  2 => 'Partly cloudy',
  3 => 'Overcast',
  45 || 48 => 'Foggy',
  51 || 53 || 55 => 'Drizzle',
  56 || 57 => 'Freezing drizzle',
  61 || 63 || 65 => 'Rain',
  66 || 67 => 'Freezing rain',
  71 || 73 || 75 || 77 => 'Snow',
  80 || 81 || 82 => 'Rain showers',
  85 || 86 => 'Snow showers',
  95 => 'Thunderstorm',
  96 || 99 => 'Thunderstorm with hail',
  _ => 'Cloudy',
};
String conditionKind(int code) => switch (code) {
  0 => 'clear',
  1 || 2 => 'partly',
  3 => 'cloudy',
  45 || 48 => 'fog',
  51 ||
  53 ||
  55 ||
  56 ||
  57 ||
  61 ||
  63 ||
  65 ||
  66 ||
  67 ||
  80 ||
  81 ||
  82 => 'rain',
  71 || 73 || 75 || 77 || 85 || 86 => 'snow',
  95 || 96 || 99 => 'storm',
  _ => 'cloudy',
};
IconData weatherIcon(String kind) => switch (kind) {
  'clear' => Icons.wb_sunny_rounded,
  'partly' => Icons.wb_cloudy_rounded,
  'rain' => Icons.grain_rounded,
  'storm' => Icons.thunderstorm_rounded,
  'snow' => Icons.ac_unit_rounded,
  'fog' => Icons.foggy,
  _ => Icons.cloud_rounded,
};

class VaultSheet extends StatefulWidget {
  const VaultSheet({super.key, this.initialCode});
  final String? initialCode;
  @override
  State<VaultSheet> createState() => _VaultSheetState();
}

class _VaultSheetState extends State<VaultSheet> {
  static const secure = FlutterSecureStorage();
  static const privacyChannel = MethodChannel('skyline_weather/privacy');
  final codeController = TextEditingController();
  Directory? root;
  bool unlocked = false, busy = false, authenticating = false;
  List<FileSystemEntity> items = [];
  String? message;
  @override
  void initState() {
    super.initState();
    _init();
  }

  @override
  void dispose() {
    unawaited(_setWindowSecure(false));
    codeController.dispose();
    super.dispose();
  }

  Future<void> _setWindowSecure(bool enabled) async {
    if (!Platform.isAndroid) return;
    try {
      await privacyChannel.invokeMethod<void>('setSecure', {
        'enabled': enabled,
      });
    } catch (_) {
      // Keep the vault usable if the platform privacy control is unavailable.
    }
  }

  Future<void> _lockVault() async {
    await _setWindowSecure(false);
    if (!mounted) return;
    setState(() {
      unlocked = false;
      codeController.clear();
      message = null;
    });
  }

  Future<void> _init() async {
    final dir = await getApplicationDocumentsDirectory();
    root = Directory('${dir.path}/saved_items');
    await root!.create(recursive: true);
    if (mounted) {
      setState(() {});
      if (widget.initialCode != null) {
        codeController.text = widget.initialCode!;
        await _unlock();
      }
    }
  }

  Future<void> _unlock() async {
    if (authenticating) return;
    final code = codeController.text.trim();
    if (code.isEmpty) {
      setState(() => message = 'Enter your vault passcode.');
      return;
    }
    setState(() => authenticating = true);
    try {
      final stored = await secure.read(key: 'vault_code');
      if (!mounted) return;
      if (stored == null) {
        if (code.length < 8 || RegExp(r'^[a-zA-Z]').hasMatch(code)) {
          setState(
            () =>
                message = 'Use 8+ characters starting with a number or symbol.',
          );
          return;
        }
        await secure.write(key: 'vault_code', value: code);
        if (!mounted) return;
        await _setWindowSecure(true);
        if (!mounted) return;
        codeController.clear();
        setState(() {
          unlocked = true;
          message = null;
        });
        _refresh();
        return;
      }

      final lockUntil =
          int.tryParse(await secure.read(key: 'vault_lockout_until') ?? '') ??
          0;
      if (!mounted) return;
      final now = DateTime.now().millisecondsSinceEpoch;
      if (lockUntil > now) {
        final seconds = ((lockUntil - now) / 1000).ceil();
        setState(
          () => message = 'Too many attempts. Try again in ${seconds}s.',
        );
        return;
      }

      if (stored == code) {
        await secure.delete(key: 'vault_failed_attempts');
        await secure.delete(key: 'vault_lockout_until');
        if (!mounted) return;
        await _setWindowSecure(true);
        if (!mounted) return;
        codeController.clear();
        setState(() {
          unlocked = true;
          message = null;
        });
        _refresh();
        return;
      }

      final attempts =
          (int.tryParse(
                await secure.read(key: 'vault_failed_attempts') ?? '',
              ) ??
              0) +
          1;
      if (!mounted) return;
      if (attempts >= 5) {
        final until = now + const Duration(seconds: 30).inMilliseconds;
        await secure.write(key: 'vault_failed_attempts', value: '0');
        await secure.write(key: 'vault_lockout_until', value: until.toString());
        if (!mounted) return;
        setState(() => message = 'Too many attempts. Try again in 30s.');
      } else {
        await secure.write(
          key: 'vault_failed_attempts',
          value: attempts.toString(),
        );
        if (!mounted) return;
        setState(
          () => message = 'Incorrect passcode. ${5 - attempts} attempts left.',
        );
      }
    } catch (_) {
      if (mounted) {
        setState(() => message = 'Secure storage is unavailable. Try again.');
      }
    } finally {
      if (mounted) setState(() => authenticating = false);
    }
  }

  Future<void> _refresh() async {
    final r = root;
    if (r == null) return;
    try {
      final list = await r.list(followLinks: false).toList();
      list.sort((a, b) => a.path.toLowerCase().compareTo(b.path.toLowerCase()));
      if (mounted) setState(() => items = list);
    } catch (_) {}
  }

  String _base(String path) => path.split(Platform.pathSeparator).last;
  Future<void> _import() async {
    try {
      setState(() {
        busy = true;
        message = null;
      });
      final picked = await FilePicker.pickFiles();
      if (root != null) {
        for (final f in picked) {
          final p = f.path;
          if (p == null) continue;
          final source = File(p);
          final dest = File('${root!.path}/${_safe(_base(p))}');
          await source.copy(await _uniquePath(dest.path));
        }
      }
    } catch (e) {
      message = 'Could not import: $e';
    } finally {
      if (mounted) {
        setState(() => busy = false);
        _refresh();
      }
    }
  }

  Future<void> _importFolder() async {
    try {
      setState(() {
        busy = true;
        message = null;
      });
      final path = await FilePicker.getDirectoryPath();
      if (path != null && root != null) {
        final source = Directory(path),
            dest = Directory(
              await _uniquePath('${root!.path}/${_safe(_base(path))}'),
            );
        await _copyDirectory(source, dest);
      }
    } catch (e) {
      message = 'Could not import folder: $e';
    } finally {
      if (mounted) {
        setState(() => busy = false);
        _refresh();
      }
    }
  }

  Future<String> _uniquePath(String path) async {
    var candidate = path, i = 2;
    while (await FileSystemEntity.type(candidate) !=
        FileSystemEntityType.notFound) {
      final dot = path.lastIndexOf('.'),
          split = dot > path.lastIndexOf(Platform.pathSeparator)
              ? dot
              : path.length;
      candidate = '${path.substring(0, split)} ($i)${path.substring(split)}';
      i++;
    }
    return candidate;
  }

  String _safe(String n) => n.replaceAll(RegExp(r'[<>:"/\\|?*]'), '_');
  Future<void> _copyDirectory(Directory from, Directory to) async {
    await to.create(recursive: true);
    await for (final entity in from.list(followLinks: false)) {
      final dest = '${to.path}/${_safe(_base(entity.path))}';
      if (entity is Directory) {
        await _copyDirectory(entity, Directory(dest));
      } else if (entity is File) {
        await entity.copy(await _uniquePath(dest));
      }
    }
  }

  Future<void> _createFolder() async {
    final name = await _askName('New folder', 'Folder name');
    if (name == null || root == null) return;
    await Directory('${root!.path}/${_safe(name)}').create(recursive: true);
    await _refresh();
  }

  Future<void> _createFile() async {
    final name = await _askName(
      'New text file',
      'File name (example: notes.txt)',
    );
    if (name == null || root == null) return;
    final fixed = name.contains('.') ? name : '$name.txt';
    await File('${root!.path}/${_safe(fixed)}').writeAsString('');
    await _refresh();
  }

  Future<String?> _askName(String title, String hint) async {
    final c = TextEditingController();
    return showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(title),
        content: TextField(
          controller: c,
          autofocus: true,
          decoration: InputDecoration(hintText: hint),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () {
              final v = c.text.trim();
              Navigator.pop(ctx, v.isEmpty ? null : v);
            },
            child: const Text('Create'),
          ),
        ],
      ),
    );
  }

  Future<void> _delete(FileSystemEntity e) async {
    await e.delete(recursive: true);
    _refresh();
  }

  Future<void> _open(FileSystemEntity e) async {
    if (e is Directory) {
      Navigator.push(
        context,
        MaterialPageRoute(
          builder: (_) => VaultFolderPage(directory: e, onRefresh: _refresh),
        ),
      );
      return;
    }
    if (e is File) {
      if (e.path.toLowerCase().endsWith('.zip')) {
        await _extractZip(e);
      } else {
        await showDialog<void>(
          context: context,
          builder: (ctx) => AlertDialog(
            title: Text(_base(e.path)),
            content: const Text(
              'This item is safely stored in your app’s private vault folder. Use your device file picker to share or open it.',
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(ctx),
                child: const Text('OK'),
              ),
            ],
          ),
        );
      }
    }
  }

  Future<void> _extractZip(File zip) async {
    try {
      final bytes = await zip.readAsBytes();
      final archive = ZipDecoder().decodeBytes(bytes);
      final folder = Directory(
        '${root!.path}/${_safe(_base(zip.path).replaceAll(RegExp(r'\.zip$', caseSensitive: false), ''))}',
      );
      await folder.create(recursive: true);
      for (final entry in archive) {
        final safe = entry.name.replaceAll('\\', '/');
        if (safe.startsWith('/') ||
            safe.split('/').any((part) => part == '..')) {
          continue;
        }
        final dest = '${folder.path}/$safe';
        if (entry.isFile) {
          final f = File(dest);
          await f.parent.create(recursive: true);
          final contents = entry.readBytes();
          if (contents != null) await f.writeAsBytes(contents);
        } else {
          await Directory(dest).create(recursive: true);
        }
      }
      await _refresh();
      setState(() => message = 'ZIP extracted into a real folder.');
    } catch (e) {
      setState(() => message = 'ZIP could not be extracted: $e');
    }
  }

  @override
  Widget build(BuildContext context) => Container(
    height: MediaQuery.sizeOf(context).height * .82,
    padding: EdgeInsets.fromLTRB(
      20,
      12,
      20,
      MediaQuery.viewInsetsOf(context).bottom + 18,
    ),
    decoration: const BoxDecoration(
      color: Color(0xff101d30),
      borderRadius: BorderRadius.vertical(top: Radius.circular(28)),
    ),
    child: Column(
      children: [
        Container(
          width: 38,
          height: 4,
          decoration: BoxDecoration(
            color: Colors.white24,
            borderRadius: BorderRadius.circular(9),
          ),
        ),
        const SizedBox(height: 18),
        Row(
          children: [
            const Icon(Icons.lock_rounded, color: mint),
            const SizedBox(width: 9),
            const Expanded(
              child: Text(
                'Private file vault',
                style: TextStyle(fontWeight: FontWeight.w800, fontSize: 18),
              ),
            ),
            if (unlocked)
              IconButton(
                onPressed: _lockVault,
                icon: const Icon(Icons.lock_outline_rounded),
                tooltip: 'Lock vault',
              ),
          ],
        ),
        const SizedBox(height: 6),
        const Text(
          'Files and folders are copied into this app’s private storage.',
          style: TextStyle(color: muted, fontSize: 12),
        ),
        const SizedBox(height: 15),
        if (!unlocked) _unlockView() else Expanded(child: _vaultView()),
        if (message != null)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Text(
              message!,
              style: const TextStyle(color: mint, fontSize: 11),
            ),
          ),
        if (busy)
          const Padding(
            padding: EdgeInsets.only(top: 10),
            child: LinearProgressIndicator(minHeight: 2),
          ),
      ],
    ),
  );
  Widget _unlockView() => Column(
    children: [
      TextField(
        controller: codeController,
        obscureText: true,
        enableSuggestions: false,
        autocorrect: false,
        keyboardType: TextInputType.visiblePassword,
        decoration: InputDecoration(
          labelText: 'Vault code',
          prefixIcon: const Icon(Icons.key_rounded),
          suffixIcon: IconButton(
            onPressed: _unlock,
            icon: const Icon(Icons.arrow_forward_rounded),
          ),
          border: OutlineInputBorder(borderRadius: BorderRadius.circular(16)),
        ),
        onSubmitted: (_) => _unlock(),
      ),
      const SizedBox(height: 10),
      const Text(
        'Use 8+ characters beginning with a number or symbol. Keep it safe; it cannot be recovered.',
        style: TextStyle(color: muted, fontSize: 11),
      ),
    ],
  );
  Widget _vaultView() => Column(
    children: [
      Wrap(
        spacing: 7,
        runSpacing: 7,
        children: [
          _action('Import files', Icons.upload_file_rounded, _import),
          _action(
            'Import folder',
            Icons.drive_folder_upload_rounded,
            _importFolder,
          ),
          _action('New folder', Icons.create_new_folder_rounded, _createFolder),
          _action('New file', Icons.note_add_rounded, _createFile),
        ],
      ),
      const SizedBox(height: 13),
      Expanded(
        child: items.isEmpty
            ? const Center(
                child: Text(
                  'Your vault is empty. Add real files or create a folder.',
                  textAlign: TextAlign.center,
                  style: TextStyle(color: muted),
                ),
              )
            : ListView.separated(
                itemCount: items.length,
                separatorBuilder: (_, _) =>
                    const Divider(height: 1, color: Color(0x16ffffff)),
                itemBuilder: (_, i) {
                  final e = items[i], dir = e is Directory;
                  return ListTile(
                    contentPadding: EdgeInsets.zero,
                    leading: CircleAvatar(
                      backgroundColor: dir
                          ? const Color(0xff263b56)
                          : const Color(0xff243244),
                      child: Icon(
                        dir
                            ? Icons.folder_rounded
                            : Icons.insert_drive_file_rounded,
                        color: dir ? const Color(0xffffce7a) : sky,
                        size: 19,
                      ),
                    ),
                    title: Text(
                      _base(e.path),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                        fontSize: 13,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    subtitle: Text(
                      dir ? 'Folder' : _fileSize(e is File ? e : null),
                      style: const TextStyle(color: muted, fontSize: 10),
                    ),
                    onTap: () => _open(e),
                    trailing: IconButton(
                      onPressed: () => _delete(e),
                      icon: const Icon(
                        Icons.delete_outline_rounded,
                        color: muted,
                        size: 20,
                      ),
                      tooltip: 'Delete',
                    ),
                  );
                },
              ),
      ),
    ],
  );
  String _fileSize(File? f) {
    if (f == null) return 'File';
    try {
      final len = f.lengthSync();
      return len > 1048576
          ? '${(len / 1048576).toStringAsFixed(1)} MB'
          : '${(len / 1024).ceil()} KB';
    } catch (_) {
      return 'File';
    }
  }

  Widget _action(String text, IconData icon, VoidCallback action) => ActionChip(
    avatar: Icon(icon, size: 16, color: sky),
    label: Text(text, style: const TextStyle(fontSize: 10)),
    onPressed: busy ? null : action,
    backgroundColor: panelLight,
    side: BorderSide(color: Colors.white.withValues(alpha: .05)),
  );
}

class VaultFolderPage extends StatefulWidget {
  const VaultFolderPage({
    super.key,
    required this.directory,
    required this.onRefresh,
  });
  final Directory directory;
  final VoidCallback onRefresh;
  @override
  State<VaultFolderPage> createState() => _VaultFolderPageState();
}

class _VaultFolderPageState extends State<VaultFolderPage> {
  List<FileSystemEntity> children = [];
  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final list = await widget.directory.list().toList();
      if (mounted) setState(() => children = list);
    } catch (_) {}
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    backgroundColor: ink,
    appBar: AppBar(
      title: Text(widget.directory.path.split(Platform.pathSeparator).last),
      backgroundColor: ink,
    ),
    body: ListView.builder(
      itemCount: children.length,
      itemBuilder: (_, i) {
        final e = children[i], dir = e is Directory;
        return ListTile(
          leading: Icon(
            dir ? Icons.folder : Icons.insert_drive_file,
            color: sky,
          ),
          title: Text(e.path.split(Platform.pathSeparator).last),
          onTap: dir
              ? () => Navigator.push(
                  context,
                  MaterialPageRoute(
                    builder: (_) => VaultFolderPage(
                      directory: e,
                      onRefresh: widget.onRefresh,
                    ),
                  ),
                )
              : null,
          trailing: IconButton(
            icon: const Icon(Icons.delete_outline_rounded),
            onPressed: () async {
              await e.delete(recursive: true);
              await _load();
              widget.onRefresh();
            },
          ),
        );
      },
    ),
  );
}
