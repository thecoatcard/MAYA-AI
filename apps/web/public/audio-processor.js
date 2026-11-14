/**
 * AudioWorkletProcessor downsampling microphone input to 16 kHz 16-bit mono PCM
 * emitted in 100 ms buffers (1,600 samples / 3,200 bytes) with client-side Voice Activity Detection (VAD).
 */

class AudioProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.targetSampleRate = 16000;
    this.bufferSize = 1600; // 100 ms at 16 kHz
    this.outputBuffer = new Int16Array(this.bufferSize);
    this.bufferIndex = 0;
    this.resampleIndex = 0;
    this.sampleSum = 0;
    this.sampleCount = 0;

    // VAD State Machine
    this.isSpeaking = false;
    this.silentBufferCount = 0;
    this.speechThreshold = 0.012; // Energy RMS threshold for human speech
    this.silenceHangoverBuffers = 7; // 7 * 100ms = 700ms consecutive silence signals turn completion
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0) {
      return true;
    }

    const channelData = input[0];
    if (!channelData || channelData.length === 0) {
      return true;
    }

    const inputSampleRate = globalThis.sampleRate || 48000;
    const ratio = inputSampleRate / this.targetSampleRate;

    for (let i = 0; i < channelData.length; i++) {
      this.sampleSum += channelData[i];
      this.sampleCount += 1;
      this.resampleIndex += 1;

      if (this.resampleIndex >= ratio) {
        this.resampleIndex -= ratio;

        // Simple anti-alias boxcar averaging before decimation
        const avgSample =
          this.sampleCount > 0 ? this.sampleSum / this.sampleCount : 0;
        this.sampleSum = 0;
        this.sampleCount = 0;

        // Clamp float sample [-1.0, 1.0] to int16 [-32768, 32767]
        const sample = Math.max(-1.0, Math.min(1.0, avgSample));
        const int16 =
          sample < 0 ? Math.round(sample * 32768) : Math.round(sample * 32767);

        this.outputBuffer[this.bufferIndex++] = int16;

        if (this.bufferIndex >= this.bufferSize) {
          // Compute RMS energy of this 100 ms buffer
          let sumSquares = 0;
          for (let j = 0; j < this.bufferSize; j++) {
            const norm = this.outputBuffer[j] / 32768.0;
            sumSquares += norm * norm;
          }
          const rms = Math.sqrt(sumSquares / this.bufferSize);

          if (rms >= this.speechThreshold) {
            this.silentBufferCount = 0;
            if (!this.isSpeaking) {
              this.isSpeaking = true;
              this.port.postMessage({ type: "VAD_SPEECH_START", rms });
            }
          } else {
            if (this.isSpeaking) {
              this.silentBufferCount += 1;
              if (this.silentBufferCount >= this.silenceHangoverBuffers) {
                this.isSpeaking = false;
                this.silentBufferCount = 0;
                this.port.postMessage({ type: "VAD_SPEECH_STOP" });
              }
            }
          }

          // Send 100 ms buffer across port
          this.port.postMessage(this.outputBuffer.buffer, [
            this.outputBuffer.buffer,
          ]);
          this.outputBuffer = new Int16Array(this.bufferSize);
          this.bufferIndex = 0;
        }
      }
    }

    return true;
  }
}

registerProcessor("audio-processor", AudioProcessor);
