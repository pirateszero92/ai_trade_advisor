import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:dio/dio.dart';
import '../../app/theme.dart';
import '../../core/api/api_client.dart';

class ChatScreen extends ConsumerStatefulWidget {
  const ChatScreen({super.key});

  @override
  ConsumerState<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends ConsumerState<ChatScreen> {
  final _controller = TextEditingController();
  final _scrollController = ScrollController();
  final List<Map<String, String>> _messages = [
    {
      'role': 'assistant',
      'content': 'สวัสดีครับ ผม Apex AI advisor ของคุณ ตลาดวันนี้มีอะไรให้ผมช่วยวิเคราะห์ไหมครับ?',
    },
  ];

  @override
  void dispose() {
    _controller.dispose();
    _scrollController.dispose();
    super.dispose();
  }

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
              child: const Text(
                'A',
                style: TextStyle(color: AppColors.bullish, fontWeight: FontWeight.bold),
              ),
            ),
            const SizedBox(width: 10),
            const Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('Apex', style: TextStyle(fontSize: 15, fontWeight: FontWeight.bold)),
                Text('AI Trading Advisor', style: TextStyle(fontSize: 11, color: Colors.white54)),
              ],
            ),
          ],
        ),
      ),
      body: Column(
        children: [
          Expanded(
            child: ListView.builder(
              controller: _scrollController,
              padding: const EdgeInsets.all(12),
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
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      child: Row(
        children: [
          Expanded(
            child: TextField(
              controller: _controller,
              decoration: const InputDecoration(
                hintText: 'ถาม Apex เกี่ยวกับตลาด...',
                hintStyle: TextStyle(color: Colors.white30),
                border: InputBorder.none,
              ),
              style: const TextStyle(color: Colors.white),
              maxLines: null,
              onSubmitted: (_) => _sendMessage(),
            ),
          ),
          IconButton(
            icon: const Icon(Icons.send, color: AppColors.bullish),
            onPressed: _sendMessage,
          ),
        ],
      ),
    );
  }

  Future<void> _sendMessage() async {
    final text = _controller.text.trim();
    if (text.isEmpty) return;

    setState(() {
      _messages.add({'role': 'user', 'content': text});
      _messages.add({'role': 'assistant', 'content': 'กำลังวิเคราะห์โครงสร้างตลาด...'});
    });
    _controller.clear();

    try {
      final dio = Dio();
      final chatMessages = _messages
          .where((m) => m['content'] != 'กำลังวิเคราะห์โครงสร้างตลาด...')
          .map((m) => {'role': m['role'], 'content': m['content']})
          .toList();

      final resp = await dio.post(
        AppApi.url('/api/v1/settings/llm/chat'),
        data: {'messages': chatMessages},
      );

      final reply = resp.data['response'] as String? ?? 'ขออภัยครับ เกิดข้อผิดพลาดในการประมวลผลคำตอบ';

      setState(() {
        _messages.removeLast();
        _messages.add({'role': 'assistant', 'content': reply});
      });
    } catch (e) {
      setState(() {
        _messages.removeLast();
        _messages.add({
          'role': 'assistant',
          'content': '⚠️ ไม่สามารถเชื่อมต่อกับ AI Engine ได้: $e\nกรุณาตรวจสอบการตั้งค่า Provider ในหน้า Settings',
        });
      });
    }

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
}

class _MessageBubble extends StatelessWidget {
  final Map<String, String> message;
  const _MessageBubble({required this.message});

  @override
  Widget build(BuildContext context) {
    final isUser = message['role'] == 'user';
    return Align(
      alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.only(bottom: 12),
        padding: const EdgeInsets.all(12),
        constraints: BoxConstraints(maxWidth: MediaQuery.of(context).size.width * 0.78),
        decoration: BoxDecoration(
          color: isUser ? AppColors.bullish.withOpacity(0.2) : AppColors.surface,
          borderRadius: BorderRadius.circular(14),
          border: isUser ? Border.all(color: AppColors.bullish.withOpacity(0.3)) : null,
        ),
        child: Text(
          message['content']!,
          style: TextStyle(
            color: isUser ? AppColors.bullish : Colors.white70,
            fontSize: 13,
            height: 1.5,
          ),
        ),
      ),
    );
  }
}
