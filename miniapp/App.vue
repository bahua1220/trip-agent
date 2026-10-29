<script>
export default {
  onLaunch() {
    console.log('智能旅行助手启动')
  }
}
</script>

<style lang="scss">
page {
  --brand: #2a76aa;
  --brand-dark: #1d5d88;
  --brand-soft: #dceef4;
  --accent: #f45f4d;
  --accent-soft: #fff0c7;
  --surface: #fffefa;
  --background: #fbf8ef;
  --text: #112733;
  --muted: #6d7e84;
  --border: #d9d4c6;
  --success: #4f936f;
  --success-soft: #e8f0df;
  --warning: #c87925;
  min-height: 100%;
  background: var(--background);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif;
  font-size: 28rpx;
}

view,
text,
input,
textarea,
button,
scroll-view,
picker {
  box-sizing: border-box;
}

button::after {
  border: none;
}

.page-shell {
  min-height: 100vh;
  padding: calc(var(--status-bar-height) + 28rpx) 40rpx 140rpx;
  background: var(--background);
}

.surface-card {
  background: var(--surface);
  border: 2rpx solid var(--border);
  border-radius: 40rpx;
}

.primary-button {
  height: 108rpx;
  line-height: 108rpx;
  margin: 0;
  border-radius: 34rpx;
  background: var(--accent);
  color: #fff;
  font-size: 30rpx;
  font-weight: 600;
}

.secondary-button {
  height: 96rpx;
  line-height: 92rpx;
  margin: 0;
  border: 2rpx solid var(--border);
  border-radius: 34rpx;
  background: var(--surface);
  color: var(--brand);
  font-size: 28rpx;
  font-weight: 600;
}

.bottom-nav {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 20;
  height: calc(120rpx + env(safe-area-inset-bottom));
  padding-bottom: env(safe-area-inset-bottom);
  display: flex;
  align-items: center;
  justify-content: space-around;
  background: rgba(255, 254, 250, 0.98);
  border-top: 2rpx solid var(--border);
}

.bottom-nav__item {
  width: 33.333%;
  text-align: center;
  color: var(--muted);
  font-size: 25rpx;
}

.bottom-nav__item.active {
  color: var(--brand);
  font-weight: 700;
}
</style>
