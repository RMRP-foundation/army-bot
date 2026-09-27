from .dismissal import (
    DismissalApplyView,
    DismissalCancelButton,
    DismissalManagementButton,
)
from .leave import ICLeaveApplyView, OOCLeaveApplyView, LeaveManagementButton
from .logistics import LogisticsApplyView, LogisticsManagementButton
from .promotion import (
    PromoteButton,
    PromotionApplyView,
    PromotionManagementButton,
)
from .materials import MaterialsReportView
from .reinstatement import (
    LegacyApproveReinstatementButton,
    LegacyRejectReinstatementButton,
    ReinstatementApplyView,
    ReinstatementManagementButton,
    ReinstatementRankSelect,
)
from .role_getting import RoleApplyView, RoleManagementButton
from .sso_patrol import SSOPatrolApplyView, SSOPatrolManagementButton
from .supplies import SupplyCreateView, SupplyManageButton
from .supplies_audit import SupplyAuditView
from .timeoff import TimeoffApplyView, TimeoffCancelButton, TimeoffManagementButton
from .transfers import (
    ApproveNewDivisionButton,
    ApproveOldDivisionButton,
    RejectTransferButton,
    TransferApplyButton,
    CancelTransferButton,
)


def load_persistent_views(bot):
    bot.add_view(ReinstatementApplyView())
    bot.add_view(RoleApplyView())
    bot.add_view(SupplyCreateView())
    bot.add_view(SupplyAuditView())
    bot.add_view(DismissalApplyView())
    bot.add_view(TimeoffApplyView())
    bot.add_view(SSOPatrolApplyView())
    bot.add_view(MaterialsReportView())
    bot.add_view(LogisticsApplyView())
    bot.add_view(ICLeaveApplyView())
    bot.add_view(OOCLeaveApplyView())
    bot.add_view(PromotionApplyView())


def load_buttons(bot):
    bot.add_dynamic_items(
        ReinstatementManagementButton,
        ReinstatementRankSelect,
        RoleManagementButton,
        SupplyManageButton,
        DismissalManagementButton,
        DismissalCancelButton,
        TransferApplyButton,
        ApproveNewDivisionButton,
        ApproveOldDivisionButton,
        RejectTransferButton,
        CancelTransferButton,
        TimeoffManagementButton,
        TimeoffCancelButton,
        SSOPatrolManagementButton,
        LogisticsManagementButton,
        LeaveManagementButton,
        PromotionManagementButton,
        PromoteButton,
        LegacyApproveReinstatementButton,  # Временная
        LegacyRejectReinstatementButton,  # Временная
    )
    load_persistent_views(bot)
