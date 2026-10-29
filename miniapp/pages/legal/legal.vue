<template>
  <view class="legal-page">
    <view class="page-header">
      <text class="back" @click="goBack">‹</text>
      <text class="header-title">{{ document.title }}</text>
    </view>

    <scroll-view class="legal-scroll" scroll-y>
      <view class="legal-card">
        <text class="updated">更新及生效日期：2026 年 8 月 5 日</text>
        <view v-for="section in document.sections" :key="section.title" class="section">
          <text class="section-title">{{ section.title }}</text>
          <text class="section-content">{{ section.content }}</text>
        </view>
        <text class="operator">运营者：智能旅行助手运营者（以微信公众平台公示主体为准）</text>
      </view>
      <view class="bottom-safe"></view>
    </scroll-view>
  </view>
</template>

<script setup>
import { computed, ref } from 'vue'
import { onLoad } from '@dcloudio/uni-app'

const type = ref('privacy')

const privacy = {
  title: '隐私政策',
  sections: [
    {
      title: '一、我们收集的信息',
      content: '为完成登录、生成与同步行程，我们会处理微信 OpenID/UnionID（如微信返回）、用户名、加密后的密码，以及你主动填写的目的地、日期、到离时间、交通方式、预算、偏好、路线节点和历史行程。为保障服务安全与排查故障，还会记录必要的请求时间、任务状态和异常日志。我们目前不要求上传头像、通讯录或精确定位。'
    },
    {
      title: '二、使用目的与第三方服务',
      content: '上述信息用于身份验证、跨端同步、路线与日程生成、地图地点查询、天气与门票信息补充、内容安全检查和故障处理。为提供这些功能，我们可能向微信、人工智能模型服务、高德地图、Open-Meteo、Tavily 或公开信息检索服务发送完成当前查询所必需的旅行条件或地点名称；不会向这些服务发送你的密码或登录令牌。各服务对数据的处理还受其自身规则约束。'
    },
    {
      title: '三、存储与保护',
      content: '账号和账户行程通常保存至你主动删除行程或注销账号；生成任务和安全日志仅在提供服务、排查故障及法律要求所需期限内保留。密码只保存不可逆哈希。我们采用访问控制、传输加密和最小权限等措施保护数据，但互联网服务无法承诺绝对安全。'
    },
    {
      title: '四、你的权利',
      content: '你可以在“我的—历史行程”查看或删除行程，在“我的账户”注销账号。注销会删除账号、登录身份、历史行程和任务数据，法律法规要求保留的记录除外。若当前仍有生成任务，需等待任务结束后再注销。你也可以通过“我的账户—意见反馈”联系我们处理访问、更正、删除或投诉请求。'
    },
    {
      title: '五、未成年人',
      content: '未满 14 周岁的用户应在监护人同意和指导下使用本服务。监护人发现未成年人信息被不当处理时，可通过意见反馈联系我们。'
    },
    {
      title: '六、人工智能生成内容',
      content: '行程、建议和说明可能由人工智能生成，页面会以“AI 生成内容”进行标识。此类内容仅供参考，不代表专业或官方结论。我们可能依据法律法规、平台规则或服务变化更新本政策，并在重要变更时再次提示。'
    }
  ]
}

const terms = {
  title: '用户协议',
  sections: [
    {
      title: '一、服务说明',
      content: '智能旅行助手根据你填写的旅行条件生成路线、日程、住宿、餐饮、天气和预算参考，并提供历史行程同步与编辑能力。部分信息来自第三方服务，可能因营业时间、天气、交通、价格或接口变化而延迟或不准确。'
    },
    {
      title: '二、人工智能内容提示',
      content: '带有“AI 生成内容”标识的路线与建议由人工智能生成，仅供出行决策参考。你应在出发前通过景区、交通、酒店等官方渠道核验开放时间、预约要求、票价和安全提示；涉及高原、极端天气、自驾或健康风险时，应结合自身情况谨慎决定。'
    },
    {
      title: '三、账号与使用规范',
      content: '你应妥善保管账号凭证，并对账号内操作负责。不得提交违法违规、侵害他人权益、虚假恶意或破坏系统安全的内容，不得绕过访问限制、批量攻击接口或将服务用于违法活动。我们可对违规内容进行拦截，并依法采取限制或停止服务等措施。'
    },
    {
      title: '四、知识产权与责任边界',
      content: '本服务的软件、界面和标识依法受到保护。你输入内容的合法权利由你负责；在提供服务所必需的范围内，你授权我们处理该内容。因不可抗力、第三方接口故障或你未核验实时信息造成的损失，我们将在法律允许范围内承担责任。'
    },
    {
      title: '五、服务变更与终止',
      content: '我们可能为安全、合规或功能升级调整服务，并尽量提前提示重大变化。你可以停止使用、删除历史行程或注销账号。对本协议或服务有疑问，可通过“我的账户—意见反馈”联系我们。'
    }
  ]
}

const document = computed(() => type.value === 'terms' ? terms : privacy)

onLoad(options => {
  type.value = options?.type === 'terms' ? 'terms' : 'privacy'
})

function goBack() {
  const pages = getCurrentPages()
  if (pages.length > 1) uni.navigateBack()
  else uni.reLaunch({ url: '/pages/index/index' })
}
</script>

<style scoped>
.legal-page {
  height: 100vh;
  display: flex;
  flex-direction: column;
  background: #f8f7fc;
}

.page-header {
  padding: calc(var(--status-bar-height) + 12rpx) 32rpx 8rpx;
  min-height: 76rpx;
  display: flex;
  align-items: center;
  flex-shrink: 0;
}

.back {
  width: 58rpx;
  color: #302a45;
  font-size: 52rpx;
}

.header-title {
  color: #252038;
  font-size: 34rpx;
  font-weight: 700;
}

.legal-scroll {
  flex: 1;
  min-height: 0;
}

.legal-card {
  margin: 20rpx 28rpx;
  padding: 34rpx 30rpx;
  border: 2rpx solid #e9e6f2;
  border-radius: 34rpx;
  background: #fff;
}

.updated,
.operator {
  display: block;
  color: #888194;
  font-size: 21rpx;
  line-height: 1.6;
}

.section {
  margin-top: 34rpx;
}

.section-title,
.section-content {
  display: block;
}

.section-title {
  color: #302a45;
  font-size: 27rpx;
  font-weight: 700;
}

.section-content {
  margin-top: 12rpx;
  color: #5e586b;
  font-size: 24rpx;
  line-height: 1.85;
  text-align: justify;
}

.operator {
  margin-top: 42rpx;
}

.bottom-safe {
  height: calc(32rpx + env(safe-area-inset-bottom));
}
</style>
