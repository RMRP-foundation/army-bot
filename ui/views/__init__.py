from .dismissal import (
    DismissalApplyView,
    DismissalCancelButton,
    DismissalManagementButton,
)
from .leave import ICLeaveApplyView, OOCLeaveApplyView, LeaveManagementButton
from .logistics import LogisticsApplyView, LogisticsManagementButton
from .materials import MaterialsReportView
from .reinstatement import (
    ApproveReinstatementButton,
    ReinstatementApplyView,
    ReinstatementRankSelect,
    RejectReinstatementButton,
)
from .role_getting import RoleApplyView, RoleManagementButton
from .sso_patrol import SSOPatrolApplyView, SSOPatrolManagementButton
from .supplies import SupplyCreateView, SupplyManageButton
from .supplies_audit import SupplyAuditView
from .timeoff import TimeoffApplyView, TimeoffCancelButton, TimeoffManagementButton
from .transfers import (
    ApproveTransferButton,
    OldApproveButton,
    RejectTransferButton,
    TransferApply,
)
from .transfers import (
    TransferView as TransferView,
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


def load_buttons(bot):
    bot.add_dynamic_items(
        ApproveReinstatementButton,
        ReinstatementRankSelect,
        RejectReinstatementButton,
        RoleManagementButton,
        SupplyManageButton,
        DismissalManagementButton,
        DismissalCancelButton,
        TransferApply,
        ApproveTransferButton,
        RejectTransferButton,
        OldApproveButton,
        TimeoffManagementButton,
        TimeoffCancelButton,
        SSOPatrolManagementButton,
        LogisticsManagementButton,
        LeaveManagementButton,
    )
    load_persistent_views(bot)
