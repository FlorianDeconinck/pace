#!/usr/bin/env python3
import fv3.utils.gt4py_utils as utils
import numpy as np
import gt4py.gtscript as gtscript
import fv3._config as spec
import fv3.stencils.copy_stencil as cp
import fv3.utils.corners as corners
import fv3.stencils.basic_operations as basic
import fv3.stencils.a2b_ord4 as a2b_ord4
from gt4py.gtscript import computation, interval, PARALLEL

sd = utils.sd


@utils.stencil()
def ptc_main(u: sd, va: sd, cosa_v: sd, sina_v: sd, dyc: sd, ptc: sd):
    with computation(PARALLEL), interval(...):
        ptc[0, 0, 0] = (u - 0.5 * (va[0, -1, 0] + va) * cosa_v) * dyc * sina_v


@utils.stencil()
def ptc_y_edge(u: sd, vc: sd, dyc: sd, sin_sg4: sd, sin_sg2: sd, ptc: sd):
    with computation(PARALLEL), interval(...):
        ptc[0, 0, 0] = u * dyc * sin_sg4[0, -1, 0] if vc > 0 else u * dyc * sin_sg2


@utils.stencil()
def vorticity_main(v: sd, ua: sd, cosa_u: sd, sina_u: sd, dxc: sd, vort: sd):
    with computation(PARALLEL), interval(...):
        vort[0, 0, 0] = (v - 0.5 * (ua[-1, 0, 0] + ua) * cosa_u) * dxc * sina_u


@utils.stencil()
def vorticity_x_edge(v: sd, uc: sd, dxc: sd, sin_sg3: sd, sin_sg1: sd, vort: sd):
    with computation(PARALLEL), interval(...):
        vort[0, 0, 0] = v * dxc * sin_sg3[-1, 0, 0] if uc > 0 else v * dxc * sin_sg1


@utils.stencil()
def delpc_main(vort: sd, ptc: sd, delpc: sd):
    with computation(PARALLEL), interval(...):
        delpc[0, 0, 0] = vort[0, -1, 0] - vort + ptc[-1, 0, 0] - ptc


@utils.stencil()
def corner_south_remove_extra_term(vort: sd, delpc: sd):
    with computation(PARALLEL), interval(...):
        delpc[0, 0, 0] = delpc - vort[0, -1, 0]


@utils.stencil()
def corner_north_remove_extra_term(vort: sd, delpc: sd):
    with computation(PARALLEL), interval(...):
        delpc[0, 0, 0] = delpc + vort


@gtscript.function
def damp_tmp(q, da_min_c, d2_bg, dddmp):
    tmpddd = dddmp * q
    mintmp = 0.2 if 0.2 < tmpddd else tmpddd
    maxd2 = d2_bg if d2_bg > mintmp else mintmp
    damp = da_min_c * maxd2
    return damp


@utils.stencil()
def damping_nord0_stencil(
    rarea_c: sd,
    delpc: sd,
    vort: sd,
    ke: sd,
    da_min_c: float,
    d2_bg: float,
    dddmp: float,
    dt: float,
):
    with computation(PARALLEL), interval(...):
        delpc[0, 0, 0] = rarea_c * delpc
        delpcdt = delpc * dt
        absdelpcdt = delpcdt if delpcdt >= 0 else -delpcdt
        damp = damp_tmp(absdelpcdt, da_min_c, d2_bg, dddmp)
        vort[0, 0, 0] = damp * delpc
        ke[0, 0, 0] = ke + vort


@utils.stencil()
def damping_nord_highorder_stencil(
    vort: sd,
    ke: sd,
    delpc: sd,
    divg_d: sd,
    da_min_c: float,
    d2_bg: float,
    dddmp: float,
    dd8: float,
):
    with computation(PARALLEL), interval(...):
        damp = damp_tmp(vort, da_min_c, d2_bg, dddmp)
        vort = damp * delpc + dd8 * divg_d
        ke = ke + vort


@utils.stencil()
def vc_from_divg(divg_d: sd, divg_u: sd, vc: sd):
    with computation(PARALLEL), interval(...):
        vc[0, 0, 0] = (divg_d[1, 0, 0] - divg_d) * divg_u


@utils.stencil()
def uc_from_divg(divg_d: sd, divg_v: sd, uc: sd):
    with computation(PARALLEL), interval(...):
        uc[0, 0, 0] = (divg_d[0, 1, 0] - divg_d) * divg_v


@utils.stencil()
def redo_divg_d(uc: sd, vc: sd, divg_d: sd):
    with computation(PARALLEL), interval(...):
        divg_d[0, 0, 0] = uc[0, -1, 0] - uc + vc[-1, 0, 0] - vc


# TODO: can we make this a stencil? docs refer to IR native functions...
def smagorinksy_diffusion_approx(delpc, vort, dt):
    # abs(dt) * sqrt(delpc**2 + vort **2)
    interface = (
        slice(spec.grid.is_, spec.grid.ie + 2),
        slice(spec.grid.js, spec.grid.je + 2),
        slice(0, spec.grid.npz + 1),
    )
    vort[interface] = abs(dt) * np.sqrt(delpc[interface] ** 2 + vort[interface] ** 2)


def vorticity_calc(wk, vort, delpc, dt, nord):
    if nord != 0:
        if spec.namelist["dddmp"] < 1e-5:
            vort[:, :, :] = 0
        else:
            if spec.namelist["grid_type"] < 3:
                a2b_ord4.compute(wk, vort, False)
                smagorinksy_diffusion_approx(delpc, vort, dt)
            else:
                raise Exception("Not implemented, smag_corner")
    # return wk, vort


def nord_compute(data, nord_column):
    # utils.compute_column_split(
    #     compute, data, nord_column, "nord", ["vort", "ke", "delpc"], grid
    # )
    raise NotImplementedError()


def compute(u, v, va, ptc, vort, ua, divg_d, vc, uc, delpc, ke, wk, d2_bg, dt, nord):
    grid = spec.grid
    is2 = max(grid.halo + 1, grid.is_)
    ie1 = min(grid.npx + 1, grid.ie + 1)
    nord = int(nord)
    if nord == 0:
        damping_zero_order(
            u, v, va, ptc, vort, ua, vc, uc, delpc, ke, d2_bg, dt, is2, ie1
        )
    else:
        cp.copy_stencil(
            divg_d,
            delpc,
            origin=grid.compute_origin(),
            domain=grid.domain_shape_compute_buffer_2d(),
        )
        for n in range(1, nord + 1):
            nt = nord - n
            nint = grid.nic + 2 * nt + 1
            njnt = grid.njc + 2 * nt + 1
            js = grid.js - nt
            is_ = grid.is_ - nt
            fillc = (
                (n != nord)
                and spec.namelist["grid_type"] < 3
                and not grid.nested
                and (
                    grid.sw_corner or grid.se_corner or grid.ne_corner or grid.nw_corner
                )
            )
            if fillc:
                corners.fill_corners_2d(divg_d, grid, "B", "x")
            vc_from_divg(
                divg_d,
                grid.divg_u,
                vc,
                origin=(is_ - 1, js, 0),
                domain=(nint + 1, njnt, grid.npz),
            )
            if fillc:
                corners.fill_corners_2d(divg_d, grid, "B", "y")
            uc_from_divg(
                divg_d,
                grid.divg_v,
                uc,
                origin=(is_, js - 1, 0),
                domain=(nint, njnt + 1, grid.npz),
            )
            if fillc:
                corners.fill_corners_dgrid(vc, uc, grid, True)

            redo_divg_d(
                uc, vc, divg_d, origin=(is_, js, 0), domain=(nint, njnt, grid.npz)
            )

            if grid.sw_corner:
                corner_south_remove_extra_term(
                    uc,
                    divg_d,
                    origin=(grid.is_, grid.js, 0),
                    domain=grid.corner_domain(),
                )
            if grid.se_corner:
                corner_south_remove_extra_term(
                    uc,
                    divg_d,
                    origin=(grid.ie + 1, grid.js, 0),
                    domain=grid.corner_domain(),
                )
            if grid.ne_corner:
                corner_north_remove_extra_term(
                    uc,
                    divg_d,
                    origin=(grid.ie + 1, grid.je + 1, 0),
                    domain=grid.corner_domain(),
                )
            if grid.nw_corner:
                corner_north_remove_extra_term(
                    uc,
                    divg_d,
                    origin=(grid.is_, grid.je + 1, 0),
                    domain=grid.corner_domain(),
                )
            if not grid.stretched_grid:
                basic.adjustmentfactor_stencil(
                    grid.rarea_c,
                    divg_d,
                    origin=(is_, js, 0),
                    domain=(nint, njnt, grid.npz),
                )

        vorticity_calc(wk, vort, delpc, dt, nord)
        if grid.stretched_grid:
            dd8 = grid.da_min * spec.namelist["d4_bg"] ** (nord + 1)
        else:
            dd8 = (grid.da_min_c * spec.namelist["d4_bg"]) ** (nord + 1)
        damping_nord_highorder_stencil(
            vort,
            ke,
            delpc,
            divg_d,
            grid.da_min_c,
            d2_bg,
            spec.namelist["dddmp"],
            dd8,
            origin=grid.compute_origin(),
            domain=grid.domain_shape_compute_buffer_2d(),
        )

    return vort, ke, delpc


def damping_zero_order(u, v, va, ptc, vort, ua, vc, uc, delpc, ke, d2_bg, dt, is2, ie1):
    grid = spec.grid
    if not grid.nested:
        # TODO: ptc and vort are equivalent, but x vs y, consolidate if possible
        ptc_main(
            u,
            va,
            grid.cosa_v,
            grid.sina_v,
            grid.dyc,
            ptc,
            origin=(grid.is_ - 1, grid.js, 0),
            domain=(grid.nic + 2, grid.njc + 1, grid.npz),
        )
        y_edge_domain = (grid.nic + 2, 1, grid.npz)
        if grid.south_edge:
            ptc_y_edge(
                u,
                vc,
                grid.dyc,
                grid.sin_sg4,
                grid.sin_sg2,
                ptc,
                origin=(grid.is_ - 1, grid.js, 0),
                domain=y_edge_domain,
            )
        if grid.north_edge:
            ptc_y_edge(
                u,
                vc,
                grid.dyc,
                grid.sin_sg4,
                grid.sin_sg2,
                ptc,
                origin=(grid.is_ - 1, grid.je + 1, 0),
                domain=y_edge_domain,
            )

        vorticity_main(
            v,
            ua,
            grid.cosa_u,
            grid.sina_u,
            grid.dxc,
            vort,
            origin=(is2, grid.js - 1, 0),
            domain=(ie1 - is2 + 1, grid.njc + 2, grid.npz),
        )
        x_edge_domain = (1, grid.njc + 2, grid.npz)
        if grid.west_edge:
            vorticity_x_edge(
                v,
                uc,
                grid.dxc,
                grid.sin_sg3,
                grid.sin_sg1,
                vort,
                origin=(grid.is_, grid.js - 1, 0),
                domain=x_edge_domain,
            )
        if grid.east_edge:
            vorticity_x_edge(
                v,
                uc,
                grid.dxc,
                grid.sin_sg3,
                grid.sin_sg1,
                vort,
                origin=(grid.ie + 1, grid.js - 1, 0),
                domain=x_edge_domain,
            )
    else:
        raise Exception("nested not implemented")
    delpc_main(
        vort,
        ptc,
        delpc,
        origin=grid.compute_origin(),
        domain=grid.domain_shape_compute_buffer_2d(),
    )

    if grid.sw_corner:
        corner_south_remove_extra_term(
            vort, delpc, origin=(grid.is_, grid.js, 0), domain=grid.corner_domain()
        )
    if grid.se_corner:
        corner_south_remove_extra_term(
            vort, delpc, origin=(grid.ie + 1, grid.js, 0), domain=grid.corner_domain()
        )
    if grid.ne_corner:
        corner_north_remove_extra_term(
            vort,
            delpc,
            origin=(grid.ie + 1, grid.je + 1, 0),
            domain=grid.corner_domain(),
        )
    if grid.nw_corner:
        corner_north_remove_extra_term(
            vort, delpc, origin=(grid.is_, grid.je + 1, 0), domain=grid.corner_domain()
        )

    damping_nord0_stencil(
        grid.rarea_c,
        delpc,
        vort,
        ke,
        grid.da_min_c,
        d2_bg,
        spec.namelist["dddmp"],
        dt,
        origin=grid.compute_origin(),
        domain=grid.domain_shape_compute_buffer_2d(),
    )
