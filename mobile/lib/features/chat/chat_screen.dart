import 'package:flutter/material.dart';
import 'package:flutter/scheduler.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:dio/dio.dart';
import 'package:intl/intl.dart';
import '../../app/theme.dart';
import '../../core/api/api_client.dart';

// ---------------------------------------------------------------------------
// Data model
// ---------------------------------------------------------------------------

class ChatMessage {
  final String id;
  final String role;        // "user" | "assistant"
  final String content;
  final DateTime createdAt;
  final bool isLoading;

  const ChatMessage({
    required this.id,
    required this.role,
    required this.content,
    required this.createdAt,
    this.isLoading = false,
  });

  factory ChatMessage.fromJson(Map<String, dynamic> json) => ChatMessage(
        id: json['id'] as String? ?? UniqueKey().toString(),
        role: json['role'] as String? ?? 'assistant',
        content: json['content'] as String? ?? '',
        createdAt: DateTime.tryParse(json['created_at'] as String? ?? '') ??
            DateTime.now().toUtc(),
      );

  bool get isUser => role == 'user';
}

class ChatSession {
  final String id;
  final String title;
  final String dayLabel;
  final String dayTitle;
  final int messageCount;

  const ChatSession({
    required this.id,
    required this.title,
    required this.dayLabel,
    required this.dayTitle,
    required this.messageCount,
  });

  factory ChatSession.fromJson(Map<String, dynamic> json) => ChatSession(
        id: json['id']?.toString() ?? '',
        title: json['title']?.toString() ?? '',
        dayLabel: json['day_label']?.toString() ?? '',
        dayTitle: json['day_title']?.toString() ?? '',
        messageCount: (json['message_count'] as num?)?.toInt() ?? 0,
      );
}

// ---------------------------------------------------------------------------
// Screen
// ---------------------------------------------------------------------------

class ChatScreen extends ConsumerStatefulWidget {
  const ChatScreen({super.key});

  @override
  ConsumerState<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends ConsumerState<ChatScreen> {
  final _controller = TextEditingController();
  final _scrollController = ScrollController();

  Dio get _dio => AppApi.dio;
  String? _sessionId;
  String _sessionTitle = '';
  final List<ChatMessage> _messages = [];
  bool _isLoading = false;
  bool _isHistoryLoading = true;

  // ------------- lifecycle ---------------

  @override
  void initState() {
    super.initState();
    _loadTodaySession();
  }

  @override
  void dispose() {
    _controller.dispose();
    _scrollController.dispose();
    super.dispose();
  }

  // ------------- session management ------

  Future<void> _loadTodaySession() async {
    setState(() => _isHistoryLoading = true);
    try {
      final resp = await _dio.get(AppApi.url('/api/v1/chat/sessions/today'));
      final data = resp.data as Map<String, dynamic>;
      _sessionId = data['id'] as String;
      _sessionTitle = data['title'] as String? ?? 'Chat';
      final rawMsgs = data['messages'] as List<dynamic>? ?? [];
      if (!mounted) return;
      setState(() {
        _messages.clear();
        _messages.addAll(rawMsgs
            .map((m) => ChatMessage.fromJson(m as Map<String, dynamic>))
            .toList());
        // Add welcome if empty
        if (_messages.isEmpty) {
          _messages.add(ChatMessage(
            id: 'welcome',
            role: 'assistant',
            content: 'สวัสดีครับ ผม Apex AI advisor ของคุณ 📊\nตลาดวันนี้มีอะไรให้ผมช่วยวิเคราะห์ไหมครับ?',
            createdAt: DateTime.now().toUtc(),
          ));
        }
        _isHistoryLoading = false;
      });
      _scrollToBottom();
    } catch (e) {
      if (!mounted) return;
      setState(() => _isHistoryLoading = false);
    }
  }

  Future<void> _loadSession(String sessionId) async {
    setState(() => _isHistoryLoading = true);
    try {
      final resp = await _dio.get(AppApi.url('/api/v1/chat/sessions/$sessionId'));
      final data = resp.data as Map<String, dynamic>;
      _sessionId = data['id']?.toString();
      _sessionTitle = data['title'] as String? ?? 'Chat';
      final rawMsgs = data['messages'] as List<dynamic>? ?? [];
      if (!mounted) return;
      setState(() {
        _messages.clear();
        _messages.addAll(rawMsgs
            .map((m) => ChatMessage.fromJson(m as Map<String, dynamic>))
            .toList());
        if (_messages.isEmpty) {
          _messages.add(ChatMessage(
            id: 'welcome',
            role: 'assistant',
            content: 'สวัสดีครับ ผม Apex AI advisor ของคุณ 📊\nตลาดวันนี้มีอะไรให้ผมช่วยวิเคราะห์ไหมครับ?',
            createdAt: DateTime.now().toUtc(),
          ));
        }
        _isHistoryLoading = false;
      });
      _scrollToBottom();
    } catch (e) {
      if (!mounted) return;
      setState(() => _isHistoryLoading = false);
    }
  }

  Future<void> _createNewSession() async {
    try {
      final resp = await _dio.post(AppApi.url('/api/v1/chat/sessions'), data: {
        'title': 'Chat ${DateFormat('dd MMM').format(DateTime.now())}',
      });
      final data = resp.data as Map<String, dynamic>;
      _sessionId = data['id'] as String;
      _sessionTitle = data['title'] as String? ?? 'New Chat';
      setState(() {
        _messages.clear();
        _messages.add(ChatMessage(
          id: 'welcome-new',
          role: 'assistant',
          content: 'เริ่ม session ใหม่แล้วครับ ✨ มีอะไรให้วิเคราะห์ไหมครับ?',
          createdAt: DateTime.now().toUtc(),
        ));
      });
    } catch (e) {
      _showSnack('ไม่สามารถสร้าง session ใหม่ได้: $e');
    }
  }

  Future<void> _deleteSession() async {
    if (_sessionId == null) return;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: AppColors.surface,
        title: const Text('ล้างประวัติ', style: TextStyle(color: Colors.white)),
        content: const Text('ต้องการลบประวัติการสนทนานี้ทั้งหมด?',
            style: TextStyle(color: Colors.white70)),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: const Text('ยกเลิก')),
          TextButton(
              onPressed: () => Navigator.pop(ctx, true),
              child:
                  const Text('ลบ', style: TextStyle(color: Colors.redAccent))),
        ],
      ),
    );
    if (confirmed == true) {
      await _dio.delete(AppApi.url('/api/v1/chat/sessions/$_sessionId'));
      await _createNewSession();
    }
  }

  // ------------- sending messages --------

  Future<void> _sendMessage() async {
    final text = _controller.text.trim();
    if (text.isEmpty || _isLoading) return;
    if (_sessionId == null) await _loadTodaySession();

    _controller.clear();
    final userMsg = ChatMessage(
      id: DateTime.now().millisecondsSinceEpoch.toString(),
      role: 'user',
      content: text,
      createdAt: DateTime.now().toUtc(),
    );
    final thinkingMsg = ChatMessage(
      id: 'thinking',
      role: 'assistant',
      content: '',
      createdAt: DateTime.now().toUtc(),
      isLoading: true,
    );
    setState(() {
      _messages.add(userMsg);
      _messages.add(thinkingMsg);
      _isLoading = true;
    });
    _scrollToBottom();

    try {
      // Build context window from history (last 15 messages)
      final history = _messages
          .where((m) => !m.isLoading && m.id != 'welcome' && m.id != 'welcome-new')
          .map((m) => {'role': m.role, 'content': m.content})
          .toList();

      final resp = await _dio.post(
        AppApi.url('/api/v1/settings/llm/chat'),
        data: {'messages': history},
      );

      final reply = resp.data['response'] as String? ??
          'ขออภัยครับ เกิดข้อผิดพลาดในการประมวลผลคำตอบ';

      final aiMsg = ChatMessage(
        id: DateTime.now().millisecondsSinceEpoch.toString() + '_ai',
        role: 'assistant',
        content: reply,
        createdAt: DateTime.now().toUtc(),
      );

      if (!mounted) return;
      setState(() {
        _messages.removeLast(); // remove thinking
        _messages.add(aiMsg);
        _isLoading = false;
      });
      _scrollToBottom();

      // Persist to DB
      if (_sessionId != null) {
        () async {
          try {
            await _dio.post(AppApi.url('/api/v1/chat/messages/bulk-save'), data: {
              'session_id': _sessionId,
              'user_content': text,
              'assistant_content': reply,
            });
          } catch (_) {}
        }();
      }
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _messages.removeLast();
        _messages.add(ChatMessage(
          id: DateTime.now().millisecondsSinceEpoch.toString() + '_err',
          role: 'assistant',
          content: '⚠️ ไม่สามารถเชื่อมต่อกับ AI Engine ได้\nกรุณาตรวจสอบการตั้งค่า Provider ในหน้า Settings',
          createdAt: DateTime.now().toUtc(),
        ));
        _isLoading = false;
      });
      _scrollToBottom();
    }
  }

  void _scrollToBottom() {
    SchedulerBinding.instance.addPostFrameCallback((_) {
      if (_scrollController.hasClients) {
        _scrollController.animateTo(
          _scrollController.position.maxScrollExtent + 200,
          duration: const Duration(milliseconds: 350),
          curve: Curves.easeOut,
        );
      }
    });
  }

  void _showSnack(String msg) {
    ScaffoldMessenger.of(context)
        .showSnackBar(SnackBar(content: Text(msg)));
  }

  // ------------- history drawer ----------

  void _openHistoryDrawer() => _showSessionDrawer();

  Future<void> _showSessionDrawer() async {
    try {
      final resp = await _dio.get(AppApi.url('/api/v1/chat/sessions'));
      final data = resp.data as Map<String, dynamic>;
      final grouped = data['grouped'] as List<dynamic>? ?? [];

      if (!mounted) return;
      showModalBottomSheet(
        context: context,
        backgroundColor: AppColors.surface,
        isScrollControlled: true,
        builder: (ctx) => DraggableScrollableSheet(
          expand: false,
          initialChildSize: 0.6,
          maxChildSize: 0.9,
          builder: (_, sc) => Column(
            children: [
              Container(
                width: 40,
                height: 4,
                margin: const EdgeInsets.symmetric(vertical: 12),
                decoration: BoxDecoration(
                  color: Colors.white24,
                  borderRadius: BorderRadius.circular(2),
                ),
              ),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
                child: Row(
                  children: [
                    const Text('ประวัติการสนทนา',
                        style: TextStyle(
                            color: Colors.white,
                            fontSize: 16,
                            fontWeight: FontWeight.bold)),
                    const Spacer(),
                    TextButton.icon(
                      onPressed: () {
                        Navigator.pop(ctx);
                        _createNewSession();
                      },
                      icon: const Icon(Icons.add, size: 16, color: AppColors.bullish),
                      label: const Text('ใหม่',
                          style: TextStyle(color: AppColors.bullish, fontSize: 13)),
                    ),
                  ],
                ),
              ),
              const Divider(color: Colors.white12),
              Expanded(
                child: grouped.isEmpty
                    ? const Center(
                        child: Text('ยังไม่มีประวัติการสนทนา',
                            style: TextStyle(color: Colors.white38)))
                    : ListView.builder(
                        controller: sc,
                        itemCount: grouped.length,
                        itemBuilder: (_, gi) {
                          final group = grouped[gi] as Map<String, dynamic>;
                          final dayTitle = group['day_title'] as String? ?? '';
                          final sessions =
                              group['sessions'] as List<dynamic>? ?? [];
                          return Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Padding(
                                padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
                                child: Text(dayTitle,
                                    style: const TextStyle(
                                        color: Colors.white38,
                                        fontSize: 11,
                                        fontWeight: FontWeight.w600)),
                              ),
                              ...sessions.map((s) {
                                final session = s as Map<String, dynamic>;
                                final sid = session['id'] as String;
                                final isActive = sid == _sessionId;
                                return ListTile(
                                  dense: true,
                                  leading: Icon(
                                    Icons.chat_bubble_outline,
                                    size: 18,
                                    color: isActive
                                        ? AppColors.bullish
                                        : Colors.white38,
                                  ),
                                  title: Text(
                                    session['title'] as String? ?? 'Chat',
                                    style: TextStyle(
                                        color: isActive
                                            ? AppColors.bullish
                                            : Colors.white70,
                                        fontSize: 13),
                                    maxLines: 1,
                                    overflow: TextOverflow.ellipsis,
                                  ),
                                  subtitle: Text(
                                    '${session['message_count'] ?? 0} ข้อความ',
                                    style: const TextStyle(
                                        color: Colors.white38, fontSize: 11),
                                  ),
                                  selected: isActive,
                                  selectedTileColor:
                                      AppColors.bullish.withOpacity(0.08),
                                  shape: RoundedRectangleBorder(
                                      borderRadius: BorderRadius.circular(8)),
                                  onTap: () {
                                    Navigator.pop(ctx);
                                    _loadSession(sid);
                                  },
                                );
                              }),
                            ],
                          );
                        },
                      ),
              ),
            ],
          ),
        ),
      );
    } catch (e) {
      _showSnack('ไม่สามารถโหลดประวัติได้');
    }
  }

  // ------------- build -------------------

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.background,
      appBar: AppBar(
        backgroundColor: AppColors.surface,
        title: Row(
          children: [
            CircleAvatar(
              backgroundColor: AppColors.bullish.withOpacity(0.2),
              radius: 16,
              child: const Text('A',
                  style: TextStyle(
                      color: AppColors.bullish, fontWeight: FontWeight.bold)),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text('Apex',
                      style: TextStyle(
                          fontSize: 15, fontWeight: FontWeight.bold)),
                  Text(
                    _sessionTitle.isEmpty ? 'AI Trading Advisor' : _sessionTitle,
                    style:
                        const TextStyle(fontSize: 11, color: Colors.white54),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                ],
              ),
            ),
          ],
        ),
        actions: [
          // History drawer
          IconButton(
            icon: const Icon(Icons.history, size: 20, color: Colors.white54),
            tooltip: 'ประวัติการสนทนา',
            onPressed: _openHistoryDrawer,
          ),
          // New session
          IconButton(
            icon: const Icon(Icons.add_comment_outlined,
                size: 20, color: Colors.white54),
            tooltip: 'เริ่ม session ใหม่',
            onPressed: _createNewSession,
          ),
          // Delete session
          IconButton(
            icon: const Icon(Icons.delete_outline,
                size: 20, color: Colors.white38),
            tooltip: 'ล้างประวัติ',
            onPressed: _deleteSession,
          ),
        ],
      ),
      body: _isHistoryLoading
          ? const Center(
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  CircularProgressIndicator(
                      color: AppColors.bullish, strokeWidth: 2),
                  SizedBox(height: 12),
                  Text('กำลังโหลดประวัติการสนทนา...',
                      style: TextStyle(color: Colors.white38, fontSize: 13)),
                ],
              ),
            )
          : Column(
              children: [
                Expanded(
                  child: ListView.builder(
                    controller: _scrollController,
                    padding: const EdgeInsets.fromLTRB(12, 12, 12, 8),
                    itemCount: _messages.length,
                    itemBuilder: (ctx, i) => _MessageBubble(message: _messages[i]),
                  ),
                ),
                _buildInputBar(),
              ],
            ),
    );
  }

  Widget _buildInputBar() {
    return Container(
      color: AppColors.surface,
      padding: const EdgeInsets.fromLTRB(12, 8, 8, 12),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.end,
        children: [
          Expanded(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxHeight: 120),
              child: TextField(
                controller: _controller,
                decoration: const InputDecoration(
                  hintText: 'ถาม Apex เกี่ยวกับตลาด...',
                  hintStyle: TextStyle(color: Colors.white30),
                  border: InputBorder.none,
                  contentPadding:
                      EdgeInsets.symmetric(horizontal: 4, vertical: 8),
                ),
                style: const TextStyle(color: Colors.white, fontSize: 14),
                maxLines: null,
                textInputAction: TextInputAction.newline,
                onSubmitted: (_) => _sendMessage(),
              ),
            ),
          ),
          const SizedBox(width: 4),
          AnimatedSwitcher(
            duration: const Duration(milliseconds: 200),
            child: _isLoading
                ? const SizedBox(
                    width: 36,
                    height: 36,
                    child: Padding(
                      padding: EdgeInsets.all(8),
                      child: CircularProgressIndicator(
                          strokeWidth: 2, color: AppColors.bullish),
                    ),
                  )
                : IconButton(
                    icon: const Icon(Icons.send_rounded, color: AppColors.bullish),
                    onPressed: _sendMessage,
                  ),
          ),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Message Bubble
// ---------------------------------------------------------------------------

class _MessageBubble extends StatelessWidget {
  final ChatMessage message;
  const _MessageBubble({required this.message});

  @override
  Widget build(BuildContext context) {
    if (message.isLoading) return const _TypingIndicator();

    final isUser = message.isUser;
    final timeStr = DateFormat('HH:mm').format(message.createdAt.toLocal());

    return Align(
      alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
      child: Column(
        crossAxisAlignment:
            isUser ? CrossAxisAlignment.end : CrossAxisAlignment.start,
        children: [
          Container(
            margin: const EdgeInsets.only(bottom: 2, top: 8),
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
            constraints: BoxConstraints(
                maxWidth: MediaQuery.of(context).size.width * 0.80),
            decoration: BoxDecoration(
              color: isUser
                  ? AppColors.bullish.withOpacity(0.18)
                  : const Color(0xFF1E2533),
              borderRadius: BorderRadius.only(
                topLeft: const Radius.circular(16),
                topRight: const Radius.circular(16),
                bottomLeft:
                    isUser ? const Radius.circular(16) : const Radius.circular(4),
                bottomRight:
                    isUser ? const Radius.circular(4) : const Radius.circular(16),
              ),
              border: isUser
                  ? Border.all(color: AppColors.bullish.withOpacity(0.25))
                  : null,
            ),
            child: Text(
              message.content,
              style: TextStyle(
                color: isUser ? AppColors.bullish : Colors.white,
                fontSize: 13.5,
                height: 1.55,
              ),
            ),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 6),
            child: Text(
              timeStr,
              style: const TextStyle(color: Colors.white24, fontSize: 10),
            ),
          ),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Typing indicator (animated 3 dots)
// ---------------------------------------------------------------------------

class _TypingIndicator extends StatefulWidget {
  const _TypingIndicator();
  @override
  State<_TypingIndicator> createState() => _TypingIndicatorState();
}

class _TypingIndicatorState extends State<_TypingIndicator>
    with SingleTickerProviderStateMixin {
  late AnimationController _ctrl;

  @override
  void initState() {
    super.initState();
    _ctrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1200),
    )..repeat();
  }

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Align(
      alignment: Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.only(top: 8, bottom: 2),
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        decoration: BoxDecoration(
          color: const Color(0xFF1E2533),
          borderRadius: const BorderRadius.only(
            topLeft: Radius.circular(16),
            topRight: Radius.circular(16),
            bottomRight: Radius.circular(16),
            bottomLeft: Radius.circular(4),
          ),
        ),
        child: AnimatedBuilder(
          animation: _ctrl,
          builder: (_, __) {
            return Row(
              mainAxisSize: MainAxisSize.min,
              children: List.generate(3, (i) {
                final t = (_ctrl.value - i * 0.2).clamp(0.0, 1.0);
                final opacity = (t < 0.5 ? t * 2 : (1 - t) * 2).clamp(0.2, 1.0);
                return Container(
                  width: 7,
                  height: 7,
                  margin: const EdgeInsets.symmetric(horizontal: 2.5),
                  decoration: BoxDecoration(
                    color: AppColors.bullish.withOpacity(opacity),
                    shape: BoxShape.circle,
                  ),
                );
              }),
            );
          },
        ),
      ),
    );
  }
}
